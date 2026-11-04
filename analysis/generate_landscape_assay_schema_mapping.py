"""Reproduce landscape_assay_schema_mapping.csv.

This script captures the pipeline used to generate the assay-to-schema
mapping at the repo root:

1. Download and combine a set of NAMHub Landscape RecordSets from Synapse.
2. Extract the unique assays, dataset categories, Context of Use values, and
   NAM/model-system names (the last one via a single batched Claude call over
   each unique `DatasetDescription`, since there's no dedicated NAM column).
3. Join those unique assays against a hand-curated mapping of each assay to
   the most relevant JSON Schema data model, selected from a survey of all 8
   Sage Bionetworks DCC data models linked from
   https://sage-bionetworks.github.io/core-models/.

Step 3's mapping (ASSAY_SCHEMA_MAPPING below) is NOT re-derived by this
script — it was produced by manually reading schema files across 8 GitHub
repos and judging domain relevance, which isn't something this script
re-executes. It's recorded here as data so the final CSV is reproducible and
auditable. If new Landscape data introduces assays not covered below, this
mapping needs to be extended by hand (see Readme in this directory's parent
for the DCC repo locations surveyed).

Usage:
    python analysis/generate_landscape_assay_schema_mapping.py
"""

import json
import logging

import anthropic
import pandas as pd
import synapseclient
from pydantic import BaseModel
from synapseclient.models import RecordSet

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

RECORD_SET_IDS = [
    "syn74569118",
    "syn74569195",
    "syn74569207",
    "syn76030881",
    "syn75123959",
    "syn75123971",
    "syn75123966",
]

OUTPUT_CSV = "landscape_assay_schema_mapping.csv"

NAM_EXTRACTION_SYSTEM_PROMPT = """\
You extract the specific New Approach Methodology (NAM) / model system name \
from each numbered dataset description below. The NAM name is the specific \
in vitro, in silico, or ex vivo model system used (e.g. "human iPSC-derived \
cardiac organoid", "IBD human intestinal organoid co-cultured with \
macrophages (HIO:MAC)", "3D liver microtissue model", "renal proximal tubule \
MPS model"). Return one item per index, using the exact index numbers given. \
Keep names concise and specific; if a description is purely about a \
data/assay type with no model system named (e.g. "Pathway reporter gene \
assay"), infer the most specific model system implied by context, or use the \
literal text if no model system is identifiable.
"""

# Hand-curated in a prior session: for each unique assay found in the
# Landscape RecordSets, the JSON Schema (from a survey of all 8 Sage
# Bionetworks DCC data models) judged most relevant. `is_fallback=True` means
# no DCC has a real matching schema; the value shown is the closest technique
# match (or, for the two "in silico" entries, NF's explicit placeholder
# template for data types with no domain-specific schema yet).
ASSAY_SCHEMA_MAPPING = [
    {
        "assay": "3D confocal imaging",
        "dcc": "NF",
        "schema_name": "MicroscopyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MicroscopyAssayTemplate.json",
        "is_fallback": False,
        "rationale": "General-purpose microscopy schema; no DCC has a 3D-specific variant",
    },
    {
        "assay": "Atomic force microscopy",
        "dcc": "NF",
        "schema_name": "MaterialScienceAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MaterialScienceAssayTemplate.json",
        "is_fallback": True,
        "rationale": "Closest fit (surface/material characterization); no DCC has an AFM-specific schema",
    },
    {
        "assay": "Cell viability assay",
        "dcc": "NF",
        "schema_name": "PlateBasedReporterAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/PlateBasedReporterAssayTemplate.json",
        "is_fallback": True,
        "rationale": "No DCC models cell-viability readouts directly; plate-based reporter assay is the closest technique match (e.g. luminescence/colorimetric viability readouts)",
    },
    {
        "assay": "Clinical data",
        "dcc": "NF",
        "schema_name": "ClinicalAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/ClinicalAssayTemplate.json",
        "is_fallback": False,
        "rationale": "General-purpose clinical/tabular schema, not tied to one disease's scoring system",
    },
    {
        "assay": "Confocal microscopy",
        "dcc": "NF",
        "schema_name": "MicroscopyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MicroscopyAssayTemplate.json",
        "is_fallback": False,
        "rationale": "Direct general match",
    },
    {
        "assay": "ELISA",
        "dcc": "NF",
        "schema_name": "AffinityProteomicsTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/AffinityProteomicsTemplate.json",
        "is_fallback": True,
        "rationale": "No DCC has a standalone ELISA schema; antibody-based affinity proteomics is the closest technique match",
    },
    {
        "assay": "Flow cytometry",
        "dcc": "NF",
        "schema_name": "FlowCytometryTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/FlowCytometryTemplate.json",
        "is_fallback": False,
        "rationale": "Direct, general-purpose match",
    },
    {
        "assay": "Fluorescence microscopy assay",
        "dcc": "NF",
        "schema_name": "MicroscopyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MicroscopyAssayTemplate.json",
        "is_fallback": False,
        "rationale": "General microscopy match (ImmunoMicroscopy assumes antibody staining, not always implied)",
    },
    {
        "assay": "High content screen",
        "dcc": "NF",
        "schema_name": "MicroscopyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MicroscopyAssayTemplate.json",
        "is_fallback": True,
        "rationale": "No DCC models HCS specifically; automated multi-well microscopy imaging is the core underlying technique",
    },
    {
        "assay": "Histology",
        "dcc": "HTAN2",
        "schema_name": "HTAN.DigitalPathologyData",
        "schema_url": "https://github.com/ncihtan/htan2-data-model/blob/main/JSON_Schemas/v1.5.0/HTAN.DigitalPathologyData-v1.5.0-schema.json",
        "is_fallback": False,
        "rationale": "Purpose-built for histology (staining method, magnification, H&E) -- most relevant despite being tumor-atlas-oriented",
    },
    {
        "assay": "Immunofluorescence",
        "dcc": "NF",
        "schema_name": "ImmunoMicroscopyTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/ImmunoMicroscopyTemplate.json",
        "is_fallback": False,
        "rationale": "Direct, general-purpose match (antibody/assayTarget fields)",
    },
    {
        "assay": "Immunohistochemistry",
        "dcc": "NF",
        "schema_name": "ImmunoMicroscopyTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/ImmunoMicroscopyTemplate.json",
        "is_fallback": False,
        "rationale": "Direct match; general-purpose over tumor-atlas-specific HTAN2 alternative",
    },
    {
        "assay": "In silico model / modeling",
        # The raw data has 3 case/spelling variants of this same assay
        # (InsilicoModel, InsilicoModeling, Insilicomodel); all are covered
        # by this one merged entry.
        "aliases": ["InsilicoModel", "InsilicoModeling", "Insilicomodel"],
        "dcc": "NF",
        "schema_name": "GenericDataResourceTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/GenericDataResourceTemplate.json",
        "is_fallback": True,
        "rationale": "No DCC has a computational/in-silico-model assay schema; this is NF's explicit placeholder template for data types with no domain-specific schema yet",
    },
    {
        "assay": "Insilicosynthesis",
        "dcc": "NF",
        "schema_name": "GenericDataResourceTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/GenericDataResourceTemplate.json",
        "is_fallback": True,
        "rationale": "Same as In silico model/modeling -- no matching schema exists in any surveyed DCC",
    },
    {
        "assay": "Liquid chromatography/mass spectrometry",
        "dcc": "NF",
        "schema_name": "MassSpecAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MassSpecAssayTemplate.json",
        "is_fallback": False,
        "rationale": "Direct, general-purpose match",
    },
    {
        "assay": "Live imaging",
        "dcc": "NF",
        "schema_name": "MicroscopyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MicroscopyAssayTemplate.json",
        "is_fallback": False,
        "rationale": "General match; no DCC has a live/time-lapse-specific variant",
    },
    {
        "assay": "Mass spectrometry",
        "dcc": "NF",
        "schema_name": "MassSpecAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/MassSpecAssayTemplate.json",
        "is_fallback": False,
        "rationale": "Direct, general-purpose match",
    },
    {
        "assay": "Multi-electrode array",
        "dcc": "NF",
        "schema_name": "ElectrophysiologyAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/ElectrophysiologyAssayTemplate.json",
        "is_fallback": False,
        "rationale": "Direct match (MEA is an electrophysiology technique)",
    },
    {
        "assay": "RNA-seq",
        "dcc": "NF",
        "schema_name": "RNASeqTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/RNASeqTemplate.json",
        "is_fallback": False,
        "rationale": "Most general-purpose, richest, most actively maintained RNA-seq schema across DCCs surveyed",
    },
    {
        "assay": "Single-cell RNA-seq",
        "dcc": "NF",
        "schema_name": "ScRNASeqTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/ScRNASeqTemplate.json",
        "is_fallback": False,
        "rationale": "General-purpose over tumor-atlas-specific (HTAN2) or narrower DCC schemas",
    },
    {
        "assay": "Single-nucleus RNA-seq",
        "dcc": "ARK",
        "schema_name": "SnRNASeqAssayMetadataTemplate",
        "schema_url": "https://github.com/ARK-Portal/data_model/blob/main/model_json_schema/ark.SnRNASeqAssayMetadataTemplate.schema.json",
        "is_fallback": False,
        "rationale": "Only DCC surveyed with a distinct single-nucleus RNA-seq schema",
    },
    {
        "assay": "Single-cell ATAC-seq",
        "dcc": "ARK",
        "schema_name": "SnATACseqAssayMetadataTemplate",
        "schema_url": "https://github.com/ARK-Portal/data_model/blob/main/model_json_schema/ark.SnATACseqAssayMetadataTemplate.schema.json",
        "is_fallback": False,
        "rationale": "Only DCC with a single-cell/nucleus-specific ATAC-seq schema (ADKP's is bulk-only)",
    },
    {
        "assay": "Small molecule library screen",
        "dcc": "NF",
        "schema_name": "PlateBasedReporterAssayTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/PlateBasedReporterAssayTemplate.json",
        "is_fallback": True,
        "rationale": "No DCC has a dedicated library-screen schema; plate-based compound screening is the closest fit",
    },
    {
        "assay": "Spatial transcriptomics",
        "dcc": "NF",
        "schema_name": "SpatialTranscriptomicsSequencingTemplate",
        "schema_url": "https://github.com/nf-osi/nf-metadata-dictionary/blob/main/registered-json-schemas/SpatialTranscriptomicsSequencingTemplate.json",
        "is_fallback": False,
        "rationale": "General-purpose, most actively maintained, not tumor-atlas-specific (vs. HTAN2's Spatial schemas)",
    },
]


class NamedExtraction(BaseModel):
    index: int
    nam_name: str


class ExtractionList(BaseModel):
    items: list[NamedExtraction]


def _parse_maybe_json_list(value) -> list[str]:
    """Landscape RecordSet cells are sometimes a bare string, sometimes a
    JSON-encoded array of strings (per the bound schema's array<string>
    properties, e.g. ContextofUse)."""
    if pd.isna(value):
        return []
    text = str(value).strip()
    if text.startswith("["):
        try:
            return [str(v).strip() for v in json.loads(text) if str(v).strip()]
        except (json.JSONDecodeError, TypeError):
            return [text]
    return [text] if text else []


def _normalize_assay_name(name: str) -> str:
    """Normalize an assay name for comparison only (raw data values are
    camelCase/no-space, e.g. "3Dconfocalimaging", while ASSAY_SCHEMA_MAPPING
    keys are human-readable, e.g. "3D confocal imaging")."""
    return "".join(ch.lower() for ch in name if ch.isalnum())


def download_and_combine_record_sets(
    record_set_ids: list[str], synapse_client: synapseclient.Synapse
) -> pd.DataFrame:
    frames = []
    for record_set_id in record_set_ids:
        record_set = RecordSet(id=record_set_id, path="/tmp").get(
            synapse_client=synapse_client
        )
        df = pd.read_csv(record_set.path)
        df["__source_syn_id"] = record_set_id
        frames.append(df)
        log.info("%s: %d rows", record_set_id, len(df))
    return pd.concat(frames, ignore_index=True)


def unique_values(df: pd.DataFrame, column: str) -> list[str]:
    values = set()
    for cell in df[column]:
        values.update(_parse_maybe_json_list(cell))
    return sorted(values)


def extract_nam_names(
    descriptions: list[str],
    anthropic_client: anthropic.Anthropic | None = None,
    model: str = "claude-opus-4-8",
) -> dict:
    """Batch-extract a NAM/model-system name for each unique description in
    a single Claude call."""
    anthropic_client = anthropic_client or anthropic.Anthropic()
    numbered = "\n".join(f"{i}: {d}" for i, d in enumerate(descriptions))

    response = anthropic_client.messages.parse(
        model=model,
        max_tokens=8000,
        system=NAM_EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": numbered}],
        output_format=ExtractionList,
    )
    return {descriptions[item.index]: item.nam_name for item in response.parsed_output.items}


def main():
    syn = synapseclient.Synapse()
    syn.login()

    combined = download_and_combine_record_sets(RECORD_SET_IDS, syn)
    log.info("Combined %d rows across %d RecordSets", len(combined), len(RECORD_SET_IDS))

    assays = unique_values(combined, "DatasetAssay")
    categories = unique_values(combined, "DatasetCategory")
    contexts_of_use = unique_values(combined, "ContextofUse")

    descriptions = combined["DatasetDescription"].dropna().unique().tolist()
    nam_by_description = extract_nam_names(descriptions)
    nam_names = sorted(set(nam_by_description.values()))

    log.info("Unique assays: %d", len(assays))
    log.info("Unique dataset categories: %d", len(categories))
    log.info("Unique Context of Use values: %d", len(contexts_of_use))
    log.info("Unique NAM/model-system names: %d", len(nam_names))

    mapping_df = pd.DataFrame(ASSAY_SCHEMA_MAPPING).rename(
        columns={
            "assay": "Assay",
            "dcc": "DCC",
            "schema_name": "SchemaName",
            "schema_url": "SchemaURL",
            "is_fallback": "IsFallback",
            "rationale": "Rationale",
        }
    )
    mapping_df["IsFallback"] = mapping_df["IsFallback"].map({True: "Yes", False: "No"})
    mapping_df = mapping_df[
        ["Assay", "DCC", "SchemaName", "SchemaURL", "IsFallback", "Rationale"]
    ]

    mapped_keys = set()
    for row in ASSAY_SCHEMA_MAPPING:
        mapped_keys.add(_normalize_assay_name(row["assay"]))
        mapped_keys.update(_normalize_assay_name(a) for a in row.get("aliases", []))
    unmapped = sorted(a for a in assays if _normalize_assay_name(a) not in mapped_keys)
    if unmapped:
        log.warning(
            "%d assay(s) found in the data with no entry in ASSAY_SCHEMA_MAPPING -- "
            "extend the mapping by hand: %s",
            len(unmapped),
            unmapped,
        )

    mapping_df.to_csv(OUTPUT_CSV, index=False)
    log.info("Wrote %d row(s) to %s", len(mapping_df), OUTPUT_CSV)


if __name__ == "__main__":
    main()
