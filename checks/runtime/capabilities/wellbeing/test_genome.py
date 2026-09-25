import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_genome import register
from gideon.workspace.capabilities.wellbeing.genome import GenomeStore, parse_genome
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError

TSV = "\ufeff# exported sample\r\nrsid\tchromosome\tposition\tgenotype\r\nrs1\t1\t123\tAG\r\nrs2\tX\t456\t--\r\n"
VCF = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tAlice\tBob\n1\t123\trs1\tA\tG,T\t.\tPASS\t.\tDP:GT\t8:1|2\t4:./0\n"


def payload(**changes):
    result = dict(
        filename="sample.tsv",
        format="tsv",
        content=TSV,
        source="personal export",
        assembly="GRCh37",
    )
    result.update(changes)
    return result


def commit(store, data=None, request="import-1"):
    data = data or payload()
    preview = store.preview(data)
    return store.commit(
        dict(data, preview_id=preview["preview_id"], request_id=request)
    )


def annotation(**changes):
    result = dict(
        request_id="note-1",
        revision=1,
        annotation="Reviewed original call",
        annotation_source="personal review",
    )
    result.update(changes)
    return result


def test_preview_does_not_mutate_and_original_is_exact(tmp_path):
    store = GenomeStore(tmp_path)
    preview = store.preview(payload())
    assert preview["row_count"] == 2
    assert preview["duplicates"] == 0
    assert preview["variants"][0]["position"] == 123
    assert preview["variants"][1]["genotype"] == "--"
    assert store.list_sources() == []
    assert not list((tmp_path / "artifacts").glob("genome-source-*"))
    result = commit(store)
    source = result["source"]
    assert source["assembly"] == "GRCh37"
    assert source["sample"] == ""
    assert source["source"] == "personal export"
    assert source["variant_count"] == 2
    assert result["added"] == 2
    assert result["duplicates"] == 0
    assert store.original(source["id"]) == TSV.encode()
    assert source["artifact"]["sha256"] == hashlib.sha256(TSV.encode()).hexdigest()
    artifact = store.artifacts.get(source["artifact"]["slug"], version=1)
    assert artifact is not None
    assert artifact.readonly
    reopened = GenomeStore(tmp_path)
    assert reopened.get_source(source["id"]) == source
    assert reopened.original(source["id"]) == TSV.encode()
    assert reopened.list_sources() == [source]
    rows = reopened.list_variants(source["id"])
    assert rows[0]["reference"] is None
    assert rows[0]["alternate"] is None
    assert rows[0]["annotation"] == ""
    assert rows[0]["row_index"] == 3
    assert rows[0]["source_id"] == source["id"]
    assert rows[0]["revision"] == 1
    assert rows[1]["chromosome"] == "X"


def test_import_receipts_assembly_identity_and_source_replay(tmp_path):
    store = GenomeStore(tmp_path)
    first = commit(store)
    assert commit(store) == first
    repeated = commit(store, request="again")
    assert repeated["added"] == 0
    assert repeated["duplicates"] == 2
    assert store.preview(payload())["duplicates"] == 2
    changed = commit(store, payload(assembly="GRCh38"), "assembly")
    assert changed["source"]["id"] != first["source"]["id"]
    assert (
        changed["source"]["artifact"]["sha256"] == first["source"]["artifact"]["sha256"]
    )
    other = commit(store, payload(source="different origin"), "origin")
    assert other["source"]["id"] != first["source"]["id"]
    renamed = commit(store, payload(filename="renamed.tsv"), "filename")
    assert renamed["added"] == 0
    assert renamed["source"]["filename"] == "sample.tsv"
    assert len(store.list_sources()) == 3
    with pytest.raises(MeasurementError, match="Request ID already used"):
        commit(store, payload(assembly="GRCh38"))
    preview = store.preview(payload())
    with pytest.raises(MeasurementError, match="Preview changed"):
        store.commit(
            dict(
                payload(source="changed"),
                preview_id=preview["preview_id"],
                request_id="changed",
            )
        )
    assert len(store.list_sources()) == 3


def test_vcf_samples_alleles_phase_missing_calls_and_haploid(tmp_path):
    store = GenomeStore(tmp_path)
    data = payload(filename="sample.vcf", format="vcf", content=VCF, sample="Alice")
    preview = store.preview(data)
    assert preview["variants"][0]["genotype"] == "G|T"
    assert preview["sample"] == "Alice"
    result = commit(store, data)
    row = store.list_variants(result["source"]["id"])[0]
    assert row["reference"] == "A"
    assert row["alternate"] == "G,T"
    assert row["genotype"] == "G|T"
    bob = commit(store, dict(data, sample="Bob"), "bob")
    assert bob["source"]["id"] != result["source"]["id"]
    assert store.list_variants(bob["source"]["id"])[0]["genotype"] == "./A"
    haploid = VCF.replace("\tBob", "").replace("\t4:./0", "").replace("8:1|2", "8:2")
    single = store.preview(payload(format="vcf", content=haploid))
    assert single["sample"] == "Alice"
    assert single["variants"][0]["genotype"] == "T"
    missing = haploid.replace("8:2", "8:.")
    assert (
        store.preview(payload(format="vcf", content=missing))["variants"][0]["genotype"]
        == "."
    )
    deletion = haploid.replace("\tA\tG,T", "\tAC\tA,ACT")
    assert (
        store.preview(payload(format="vcf", content=deletion))["variants"][0][
            "genotype"
        ]
        == "ACT"
    )


def test_manual_annotations_preserve_source_and_history(tmp_path):
    store = GenomeStore(tmp_path)
    source = commit(store)["source"]
    row = store.list_variants(source["id"])[0]
    updated = store.annotate(row["id"], annotation())
    assert updated["revision"] == 2
    assert updated["annotation"] == "Reviewed original call"
    assert updated["annotation_source"] == "personal review"
    assert updated["genotype"] == row["genotype"]
    assert updated["created_at"] == row["created_at"]
    assert updated["source_id"] == row["source_id"]
    assert store.annotate(row["id"], annotation()) == updated
    with pytest.raises(MeasurementError, match="Request ID already used"):
        store.annotate(row["id"], annotation(annotation="different"))
    with pytest.raises(MeasurementError, match="Variant changed"):
        store.annotate(row["id"], annotation(request_id="stale"))
    cleared = store.annotate(
        row["id"], annotation(request_id="clear", revision=2, annotation="")
    )
    assert cleared["revision"] == 3
    assert cleared["annotation"] == ""
    assert store.history(row["id"]) == [row, updated, cleared]
    assert GenomeStore(tmp_path).get_variant(row["id"]) == cleared
    assert store.original(source["id"]) == TSV.encode()
    assert store.list_variants(source["id"])[0] == cleared


def test_filter_pagination_isolation_and_missing_records(tmp_path):
    store = GenomeStore(tmp_path / "one")
    source = commit(store)["source"]
    rows = store.list_variants(source["id"])
    assert store.list_variants(source["id"], chromosome="1") == [rows[0]]
    assert store.list_variants(source["id"], rsid="rs2") == [rows[1]]
    assert store.list_variants(source["id"], rsid="missing") == []
    assert store.list_variants(source["id"], limit=1, offset=1) == [rows[1]]
    assert store.list_variants(source["id"], offset=2) == []
    other = GenomeStore(tmp_path / "two")
    assert other.list_sources() == []
    for operation, identity in [
        (other.get_source, source["id"]),
        (other.list_variants, source["id"]),
        (other.original, source["id"]),
        (other.get_variant, rows[0]["id"]),
        (other.history, rows[0]["id"]),
    ]:
        with pytest.raises(MeasurementError) as caught:
            operation(identity)
        assert caught.value.status == 404
    for params in [
        dict(limit=0),
        dict(limit=501),
        dict(offset=-1),
        dict(offset=100001),
        dict(limit=True),
        dict(offset=False),
    ]:
        with pytest.raises(MeasurementError, match="pagination"):
            store.list_variants(source["id"], **params)


@pytest.mark.parametrize(
    "changes",
    [
        dict(assembly="hg19"),
        dict(assembly=None),
        dict(format="json"),
        dict(source=""),
        dict(filename=""),
        dict(content=""),
        dict(content="rs1\t1\t123\tAG"),
        dict(content=TSV.replace("123", "0")),
        dict(content=TSV.replace("123", "-1")),
        dict(content=TSV.replace("123", "1000000001")),
        dict(content=TSV.replace("\t1\t", "\t23\t")),
        dict(content=TSV.replace("AG", "ZZ")),
        dict(content=TSV.replace("AG", "AG\textra")),
        dict(content="a" * (8 * 1024 * 1024 + 1)),
        dict(sample=42),
        dict(extra=True),
    ],
)
def test_invalid_input_has_no_source_or_artifact(tmp_path, changes):
    store = GenomeStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.preview(payload(**changes))
    assert store.list_sources() == []
    assert not list((tmp_path / "artifacts").glob("genome-source-*"))


@pytest.mark.parametrize(
    "changes",
    [
        dict(sample="Nobody"),
        dict(sample=""),
        dict(content=VCF.replace("8:1|2", "8:3/0")),
        dict(content=VCF.replace("8:1|2", "8:x/0")),
        dict(content=VCF.replace("DP:GT", "DP:XX")),
        dict(content=VCF.replace("\tG,T\t", "\t<DEL>\t")),
        dict(content=VCF.replace("\tBob", "\tAlice")),
        dict(content=VCF.replace("8:1|2", "8")),
    ],
)
def test_unsupported_vcf_refused_without_silent_call_changes(tmp_path, changes):
    data = payload(format="vcf", content=VCF, sample="Alice")
    data.update(changes)
    with pytest.raises(MeasurementError):
        GenomeStore(tmp_path).preview(data)


@pytest.mark.parametrize(
    "changes",
    [
        dict(revision=True),
        dict(revision="1"),
        dict(annotation=None),
        dict(annotation="x" * 10001),
        dict(annotation_source=""),
        dict(request_id=""),
        dict(genotype="GG"),
    ],
)
def test_annotation_schema_rejects_variant_mutation(tmp_path, changes):
    store = GenomeStore(tmp_path)
    source = commit(store)["source"]
    row = store.list_variants(source["id"])[0]
    with pytest.raises(MeasurementError):
        store.annotate(row["id"], annotation(**changes))
    assert store.history(row["id"]) == [row]


def test_artifact_integrity_and_concurrent_receipts(tmp_path):
    store = GenomeStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: commit(GenomeStore(tmp_path)), range(4)))
    assert all(receipt == receipts[0] for receipt in receipts)
    source = receipts[0]["source"]
    assert len(store.list_sources()) == 1
    assert len(store.list_variants(source["id"])) == 2
    ref = source["artifact"]
    path = tmp_path / "artifacts" / ref["slug"] / "versions" / ref["filename"]
    path.write_bytes(b"changed")
    with pytest.raises(MeasurementError, match="hash mismatch"):
        store.original(source["id"])
    path.unlink()
    with pytest.raises(MeasurementError, match="unavailable"):
        store.original(source["id"])


def test_duplicate_rows_and_preview_limit(tmp_path):
    header = "rsid\tchromosome\tposition\tgenotype\n"
    content = header + "".join(f"rs{n}\t1\t{n}\tAA\n" for n in range(1, 103))
    data = payload(content=content + "rs1\t1\t1\tAA\n")
    store = GenomeStore(tmp_path)
    preview = store.preview(data)
    assert preview["row_count"] == 102
    assert len(preview["variants"]) == 100
    source = commit(store, data)["source"]
    assert source["variant_count"] == 102
    assert len(store.list_variants(source["id"])) == 100
    assert len(store.list_variants(source["id"], offset=100)) == 2
    assert store.original(source["id"]) == data["content"].encode()


@pytest.mark.asyncio
async def test_real_http_import_annotate_source_and_failure(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/genome"
        response = await client.post(base + "/preview", json=payload())
        assert response.status == 200
        preview = await response.json()
        response = await client.get(base + "/sources")
        assert await response.json() == {"sources": []}
        response = await client.post(
            base + "/commit",
            json=dict(payload(), preview_id=preview["preview_id"], request_id="http"),
        )
        assert response.status == 200
        receipt = await response.json()
        identity = receipt["source"]["id"]
        response = await client.get(base + f"/sources/{identity}")
        assert await response.json() == receipt["source"]
        response = await client.get(base + f"/sources/{identity}/original")
        assert await response.read() == TSV.encode()
        assert "attachment" in response.headers["Content-Disposition"]
        response = await client.get(base + f"/sources/{identity}/variants?chromosome=1")
        row = (await response.json())["variants"][0]
        response = await client.put(
            base + f'/variants/{row["id"]}/annotation', json=annotation()
        )
        assert response.status == 200
        updated = await response.json()
        response = await client.get(base + f'/variants/{row["id"]}')
        assert await response.json() == updated
        response = await client.get(base + f'/variants/{row["id"]}/history')
        assert (await response.json())["history"] == [row, updated]
        response = await client.put(
            base + f'/variants/{row["id"]}/annotation',
            json=annotation(request_id="stale"),
        )
        assert response.status == 409
        response = await client.get(
            base + f"/sources/{identity}/variants?limit=invalid"
        )
        assert response.status == 400
        response = await client.post(
            base + "/preview",
            data="{broken",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400
        response = await client.get(base + "/sources/missing")
        assert response.status == 404


@pytest.mark.asyncio
async def test_native_provider_uses_same_home_and_all_genome_operations(tmp_path):
    provider = WellbeingProvider(tmp_path)

    async def invoke(name, identity=None, data=None):
        result = await provider.invoke(
            "wellbeing_records",
            dict(operation="genome_" + name, id=identity, payload=data or {}),
        )
        assert result.success, result.error
        return json.loads(result.output)

    preview = await invoke("preview", data=payload())
    result = await invoke(
        "commit",
        data=dict(payload(), preview_id=preview["preview_id"], request_id="native"),
    )
    source = result["source"]
    assert await invoke("sources") == [source]
    assert await invoke("source", source["id"]) == source
    assert (
        base64.b64decode((await invoke("original", source["id"]))["content_base64"])
        == TSV.encode()
    )
    rows = await invoke("variants", source["id"], dict(limit=1))
    assert len(rows) == 1
    row = rows[0]
    assert await invoke("variant", row["id"]) == row
    updated = await invoke("annotate", row["id"], annotation())
    assert await invoke("history", row["id"]) == [row, updated]
    assert GenomeStore(tmp_path).get_variant(row["id"]) == updated
    unknown = await provider.invoke(
        "wellbeing_records", {"operation": "genome_missing"}
    )
    assert not unknown.success
    assert "Unknown" in unknown.error
    tool = (await provider.list_tools())[0]
    assert "genome_annotate" in tool.parameters["properties"]["operation"]["enum"]
    assert tool.requires_approval


def test_original_over_artifact_document_limit_uses_raw_companion(tmp_path):
    store = GenomeStore(tmp_path)
    raw = "# " + "x" * 1100000 + "\n" + TSV.removeprefix("\ufeff")
    source = commit(store, payload(content=raw))["source"]
    artifact = store.artifacts.get(source["artifact"]["slug"], version=1)
    descriptor = json.loads(artifact.content)
    assert descriptor["sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert descriptor["assembly"] == "GRCh37"
    assert len(artifact.content) < 1000
    assert len(store.original(source["id"])) > 1048576
    assert store.original(source["id"]) == raw.encode()
    assert source["variant_count"] == 2
