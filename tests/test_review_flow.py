"""Upload → AI check → results, RBAC, approval workflow, improve → v(N+1), compare."""

import sqlalchemy as sa

from app.db import pg
from app.models import Notification
from app.services.notifications import send_pending_emails
from seed.samples import sample_syllabus
from tests.conftest import create_syllabus, upload


async def _ai_syllabus(ref, client_for) -> tuple[dict, dict]:
    lead = client_for(ref["users"]["shalkar"])
    syllabus = await create_syllabus(lead, ref["courses"]["ai"].id)
    uploaded = await upload(
        lead, syllabus["id"], sample_syllabus("Artificial Intelligence", with_issues=True)
    )
    return syllabus, uploaded


async def test_upload_runs_check(ref, client_for):
    syllabus, uploaded = await _ai_syllabus(ref, client_for)
    lead = client_for(ref["users"]["shalkar"])
    assert uploaded["version"]["version"] == 1

    run = (await lead.get(f"/versions/{uploaded['version']['id']}/runs/latest")).json()
    assert run["status"] == "Completed"
    assert run["rubric_version"] == "2026.1"
    assert len(run["results"]) == 49
    assert run["issues"] == 6
    statuses = {r["criterion_id"]: r["status"] for r in run["results"]}
    assert statuses["3.4"] == statuses["7.2"] == "Cannot verify"
    assert "LO5 is not mapped to an assessment" in [i["message"] for i in run["technical"]["issues"]]

    detail = (await lead.get(f"/syllabi/{syllabus['id']}")).json()
    assert detail["ai_status"] == "Needs revision"
    assert detail["approval_stage"] == "AI Checked"
    assert detail["issues"] == 6
    assert detail["is_course_lead"] is True
    assert [t["to_stage"] for t in detail["available_transitions"]] == ["Head of Program Review"]


async def test_rejects_unsupported_files(ref, client_for):
    lead = client_for(ref["users"]["shalkar"])
    syllabus = await create_syllabus(lead, ref["courses"]["ai"].id)
    response = await lead.post(f"/syllabi/{syllabus['id']}/versions", files={"file": ("x.exe", b"MZ", "x")})
    assert response.status_code == 415


async def test_rbac_lead_vs_instructor_vs_outsider(ref, client_for):
    syllabus, uploaded = await _ai_syllabus(ref, client_for)
    instructor = client_for(ref["users"]["lee"])  # teaches AI, leads ML
    outsider = client_for(ref["users"]["karimova"])  # finance professor
    admin = client_for(ref["users"]["admin.digital"])
    provost = client_for(ref["users"]["provost"])

    assert (await instructor.get(f"/syllabi/{syllabus['id']}")).json()["is_course_lead"] is False
    data = sample_syllabus("AI", with_issues=False)
    response = await instructor.post(
        f"/syllabi/{syllabus['id']}/versions", files={"file": ("s.docx", data, "x")}
    )
    assert response.status_code == 403
    assert (await instructor.post(f"/versions/{uploaded['version']['id']}/runs")).status_code == 403

    assert (await outsider.get(f"/syllabi/{syllabus['id']}")).status_code == 404
    assert (await outsider.get(f"/versions/{uploaded['version']['id']}/file")).status_code == 404
    assert (await outsider.get("/syllabi")).json()["total"] == 0

    assert (await admin.get("/syllabi")).json()["total"] == 1
    assert (await provost.get("/syllabi", params={"approval_stage": "AI Checked"})).json()["total"] == 1
    file = await admin.get(f"/versions/{uploaded['version']['id']}/file")
    assert file.status_code == 200 and file.content.startswith(b"PK")


async def test_approval_workflow(ref, client_for):
    syllabus, _ = await _ai_syllabus(ref, client_for)
    sid = syllabus["id"]
    lead = client_for(ref["users"]["shalkar"])
    head = client_for(ref["users"]["head.cs"])
    kok = client_for(ref["users"]["kok"])
    instructor = client_for(ref["users"]["lee"])

    async def move(client, stage, comment=None):
        return await client.post(f"/syllabi/{sid}/transitions", json={"to_stage": stage, "comment": comment})

    assert (await move(head, "KOK Review")).status_code == 409  # still AI Checked
    assert (await move(instructor, "Head of Program Review")).status_code == 403
    assert (await move(lead, "Head of Program Review")).status_code == 201
    assert (await move(lead, "KOK Review")).status_code == 403
    assert (await move(head, "Professor Revised")).status_code == 422  # comment required
    assert (await move(head, "Professor Revised", "Map LO5 to an assessment.")).status_code == 201
    assert (await move(lead, "Head of Program Review")).status_code == 201
    assert (await move(head, "KOK Review")).status_code == 201
    assert (await move(kok, "Approved")).status_code == 201
    assert (await lead.get(f"/syllabi/{sid}")).json()["approval_stage"] == "Approved"

    await head.post(f"/syllabi/{sid}/comments", json={"text": "Thanks, well structured."})
    activity = (await lead.get(f"/syllabi/{sid}/activity")).json()
    kinds = [a["kind"] for a in activity]
    assert kinds.count("transition") == 6  # Draft→AI Checked + 5 human moves
    assert kinds[0] == "comment" and activity[0]["actor_name"] == "Head of Computer Science"
    assert any(a["comment"] == "Map LO5 to an assessment." for a in activity)

    inbox = (await lead.get("/notifications")).json()
    assert any("returned for revision" in n["title"] for n in inbox)
    assert any("approved" in n["title"] for n in inbox)
    assert any("waiting for KOK review" in n["title"] for n in (await kok.get("/notifications")).json())

    assert await send_pending_emails() > 0
    async with pg.session_scope() as session:
        pending = await session.scalar(
            sa.select(sa.func.count()).select_from(Notification).where(Notification.email_status == "pending")
        )
    assert pending == 0


async def test_new_version_resets_workflow(ref, client_for):
    syllabus, _ = await _ai_syllabus(ref, client_for)
    lead = client_for(ref["users"]["shalkar"])
    await lead.post(f"/syllabi/{syllabus['id']}/transitions", json={"to_stage": "Head of Program Review"})
    await upload(lead, syllabus["id"], sample_syllabus("AI", with_issues=False))
    detail = (await lead.get(f"/syllabi/{syllabus['id']}")).json()
    assert (detail["current_version"], detail["approval_stage"], detail["issues"]) == (2, "AI Checked", 0)


async def test_improve_creates_next_version(ref, client_for):
    syllabus, uploaded = await _ai_syllabus(ref, client_for)
    lead = client_for(ref["users"]["shalkar"])
    run_id = uploaded["run"]["id"]

    assert (await client_for(ref["users"]["lee"]).post(f"/runs/{run_id}/proposals")).status_code == 403
    proposals = (await lead.post(f"/runs/{run_id}/proposals")).json()
    assert {i["criterion_id"] for i in proposals["items"]} == {"1.2", "3.5", "4.3", "6.1", "6.4", "9.2"}
    assert all(i["anchor_found"] for i in proposals["items"])
    assert (await lead.post(f"/runs/{run_id}/proposals")).json()["id"] == proposals["id"]  # idempotent

    pid = proposals["id"]
    assert (await lead.post(f"/proposals/{pid}/apply")).status_code == 422  # nothing accepted
    for item in proposals["items"]:
        response = await lead.patch(f"/proposals/{pid}/items/{item['id']}", json={"decision": "accepted"})
        assert response.status_code == 200
    applied = await lead.post(f"/proposals/{pid}/apply")
    assert applied.status_code == 201, applied.text
    body = applied.json()
    assert body["version"]["version"] == 2 and body["version"]["source"] == "improve"
    assert body["appended"] == 0

    v2 = (await lead.get(f"/versions/{body['version']['id']}/runs/latest")).json()
    assert v2["status"] == "Completed" and v2["issues"] == 0
    versions = (await lead.get(f"/syllabi/{syllabus['id']}/versions")).json()
    assert [v["issue_count"] for v in versions] == [6, 0]
    v1_run = (await lead.get(f"/runs/{run_id}")).json()
    assert v1_run["issues"] == 6  # earlier review is preserved

    assert (await lead.post(f"/proposals/{pid}/apply")).status_code == 409

    diff = (
        await lead.get("/versions/compare", params={"a": versions[0]["id"], "b": versions[1]["id"]})
    ).json()
    assert (diff["issues_before"], diff["issues_after"]) == (6, 0)
    assert {c["criterion_id"] for c in diff["criterion_changes"]} == {
        "1.2",
        "3.5",
        "4.3",
        "6.1",
        "6.4",
        "9.2",
    }
    assert any(line.startswith("+LO2. Students will compare") for line in diff["text_diff"])
