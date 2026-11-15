#!/usr/bin/env python3
"""Fetch the latest NHSBSA dm+d TRUD release and publish compact gzip JSON."""
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

TRUD_API = "https://isd.digital.nhs.uk/trud/api/v1/keys/{key}/items/{item}/releases?latest"
ITEM_ID = os.environ.get("TRUD_ITEM_ID", "24")
API_KEY = os.environ["TRUD_API_KEY"]
OUT = pathlib.Path(os.environ.get("OUTPUT_DIR", "build/uk"))
OUT.mkdir(parents=True, exist_ok=True)

TABLE_SPECS = {
    "amp_amps": ("f_amp2_", "AMPS", "AMP"),
    "amp_licensed_route": ("f_amp2_", "LICENSED_ROUTE", "LIC_ROUTE"),
    "amp_ap_information": ("f_amp2_", "AP_INFORMATION", "AP_INFO"),
    "ampp_ampps": ("f_ampp2_", "AMPPS", "AMPP"),
    "ampp_appliance_pack_info": ("f_ampp2_", "APPLIANCE_PACK_INFO", "PACK_INFO"),
    "ampp_drug_product_prescrib_info": ("f_ampp2_", "DRUG_PRODUCT_PRESCRIB_INFO", "PRESCRIB_INFO"),
    "ampp_reimbursement_info": ("f_ampp2_", "REIMBURSEMENT_INFO", "REIMB_INFO"),
    "ampp_comb_content": ("f_ampp2_", "COMB_CONTENT", "CCONTENT"),
    "ingredient": ("f_ingredient2_", "INGREDIENT_SUBSTANCES", "ING"),
    "vmp_vmps": ("f_vmp2_", "VMPS", "VMP"),
    "vmp_virtual_product_ingredient": ("f_vmp2_", "VIRTUAL_PRODUCT_INGREDIENT", "VPI"),
    "vmp_ont_drug_form": ("f_vmp2_", "ONT_DRUG_FORM", "ONT"),
    "vmp_drug_form": ("f_vmp2_", "DRUG_FORM", "DFORM"),
    "vmp_drug_route": ("f_vmp2_", "DRUG_ROUTE", "DROUTE"),
    "vmp_control_drug_info": ("f_vmp2_", "CONTROL_DRUG_INFO", "CONTROL_INFO"),
    "vmpp_vmpps": ("f_vmpp2_", "VMPPS", "VMPP"),
    "vmpp_drug_tariff_info": ("f_vmpp2_", "DRUG_TARIFF_INFO", "DTINFO"),
    "vmpp_comb_content": ("f_vmpp2_", "COMB_CONTENT", "CCONTENT"),
    "vtm": ("f_vtm2_", "VIRTUAL_THERAPEUTIC_MOIETIES", "VTM"),
    "gtin_mapping": ("GTIN_XML", "AMPPS", "AMPP"),
}
LOOKUP_TABLES = [
    "COMBINATION_PACK_IND", "COMBINATION_PROD_IND", "BASIS_OF_NAME",
    "NAMECHANGE_REASON", "VIRTUAL_PRODUCT_PRES_STATUS", "CONTROL_DRUG_CATEGORY",
    "LICENSING_AUTHORITY", "UNIT_OF_MEASURE", "FORM", "ONT_FORM_ROUTE", "ROUTE",
    "DT_PAYMENT_CATEGORY", "SUPPLIER", "FLAVOUR", "COLOUR", "BASIS_OF_STRNTH",
    "REIMBURSEMENT_STATUS", "SPEC_CONT", "DND", "VIRTUAL_PRODUCT_NON_AVAIL",
    "DISCONTINUED_IND", "DF_INDICATOR", "PRICE_BASIS", "LEGAL_CATEGORY",
    "AVAILABILITY_RESTRICTION", "LICENSING_AUTHORITY_CHANGE_REASON",
]
TABLE_NAMES = list(TABLE_SPECS) + ["lookup_" + x.lower() for x in LOOKUP_TABLES]

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DrGenZ-dm-d-updater/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))

def download(url, destination):
    req = urllib.request.Request(url, headers={"User-Agent": "DrGenZ-dm-d-updater/1.0"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=180) as response, open(destination, "wb") as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            out.write(block)
    return digest.hexdigest()

def element_value(elem):
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    result = {}
    for child in children:
        value = element_value(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result

class TableWriter:
    def __init__(self, folder):
        self.files = {}
        self.counts = {}
        for table in TABLE_NAMES:
            f = open(folder / (table + ".json"), "w", encoding="utf-8")
            f.write("[")
            self.files[table] = f
            self.counts[table] = 0

    def add(self, table, record):
        f = self.files[table]
        if self.counts[table]:
            f.write(",")
        json.dump(record, f, ensure_ascii=False, separators=(",", ":"))
        self.counts[table] += 1

    def close(self):
        for table, f in self.files.items():
            f.write("]")
            f.close()

def parse_xml(stream, specs, writer):
    stack = []
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        if event == "start":
            stack.append(elem)
            continue
        parent = stack[-2] if len(stack) > 1 else None
        if parent is not None:
            spec = specs.get(parent.tag)
            if spec and elem.tag == spec[0]:
                writer.add(spec[1], element_value(elem))
            # Remove a complete record or finished section only. Keep its child
            # elements until the enclosing record has been serialized.
            if (spec and elem.tag == spec[0]) or elem.tag in specs:
                parent.remove(elem)
        stack.pop()

def main():
    payload = http_json(TRUD_API.format(key=API_KEY, item=ITEM_ID))
    releases = payload.get("releases") or []
    if payload.get("httpStatus") != 200 or not releases:
        raise RuntimeError("TRUD returned no accessible release for item " + ITEM_ID)
    release = releases[0]
    release_id = release.get("id", "")
    release_name = release.get("name", "")
    release_date = release.get("releaseDate", "")
    archive_url = release.get("archiveFileUrl")
    if not archive_url:
        raise RuntimeError("TRUD release response did not include archiveFileUrl")

    with tempfile.TemporaryDirectory(prefix="dmd-") as temp_dir:
        temp = pathlib.Path(temp_dir)
        archive_path = temp / "nhsbsa_dmd.zip"
        archive_sha = download(archive_url, archive_path)
        expected = (release.get("archiveFileSha256") or "").lower()
        if expected and archive_sha.lower() != expected:
            raise RuntimeError("NHS release archive SHA-256 verification failed")

        writer = TableWriter(temp)
        includes_gtin = False
        try:
            with zipfile.ZipFile(archive_path) as outer:
                names = outer.namelist()
                prefixes = sorted(set(spec[0] for spec in TABLE_SPECS.values() if spec[0] != "GTIN_XML"))
                for prefix in prefixes:
                    candidate = next((n for n in names if n.startswith(prefix) and n.endswith(".xml")), None)
                    if not candidate:
                        continue
                    specs = {
                        parent_tag: (record_tag, table)
                        for table, (table_prefix, parent_tag, record_tag) in TABLE_SPECS.items()
                        if table_prefix == prefix
                    }
                    with outer.open(candidate) as xml:
                        parse_xml(xml, specs, writer)

                lookup_file = next((n for n in names if n.startswith("f_lookup2_") and n.endswith(".xml")), None)
                if lookup_file:
                    specs = {name: ("INFO", "lookup_" + name.lower()) for name in LOOKUP_TABLES}
                    with outer.open(lookup_file) as xml:
                        parse_xml(xml, specs, writer)

                gtin_zip_name = next((n for n in names if n.lower().endswith("gtin.zip")), None)
                if gtin_zip_name:
                    with outer.open(gtin_zip_name) as inner_bytes:
                        inner_data = inner_bytes.read()
                    with zipfile.ZipFile(io.BytesIO(inner_data)) as inner:
                        gtin_xml = next((n for n in inner.namelist() if n.startswith("f_gtin") and n.endswith(".xml")), None)
                        if gtin_xml:
                            with inner.open(gtin_xml) as xml:
                                parse_xml(xml, {"AMPPS": ("AMPP", "gtin_mapping")}, writer)
                            includes_gtin = True
        finally:
            writer.close()

        required = ("amp_amps", "ampp_ampps", "vmp_vmps", "vmpp_vmpps", "ingredient", "vtm")
        if any(writer.counts[name] == 0 for name in required):
            raise RuntimeError("One or more required dm+d tables were empty; refusing to publish")

        metadata = {
            "source": "NHSBSA dm+d",
            "release": release_name or release_id,
            "release_id": release_id,
            "release_date": release_date,
            "source_item_id": ITEM_ID,
            "source_archive_sha256": archive_sha,
            "includes_gtin_mapping": includes_gtin,
            "format": "JSON compressed with gzip",
            "note": "Original dm+d identifiers, source element names and GTIN date ranges are preserved. NHS medicine price records are excluded from this searchable catalogue."
        }
        json_path = OUT / "medicine_catalog_uk.json.gz"
        with gzip.open(json_path, "wt", encoding="utf-8", compresslevel=9) as out:
            out.write('{"metadata":')
            json.dump(metadata, out, ensure_ascii=False, separators=(",", ":"))
            out.write(',"tables":{')
            for index, table in enumerate(TABLE_NAMES):
                if index:
                    out.write(",")
                json.dump(table, out)
                out.write(":")
                with open(temp / (table + ".json"), "r", encoding="utf-8") as src:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
            out.write("}}")

        catalogue_sha = hashlib.sha256(json_path.read_bytes()).hexdigest()
        manifest = {
            "source": "NHSBSA dm+d",
            "release": release_name or release_id,
            "release_id": release_id,
            "release_date": release_date,
            "catalogue_url": "https://raw.githubusercontent.com/techbaggg/dmd-uk-database/catalog-data/uk/medicine_catalog_uk.json.gz",
            "sha256": catalogue_sha,
            "size_bytes": json_path.stat().st_size,
            "format": "application/gzip",
            "schema_version": 1,
        }
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("Published candidate:", release_id, release_date)
        print("Catalogue bytes:", json_path.stat().st_size)
        print("SHA-256:", catalogue_sha)
        print("Core record counts:", {k: writer.counts[k] for k in required + ("gtin_mapping",)})

if __name__ == "__main__":
    main()
