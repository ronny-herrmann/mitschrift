"""Protokoll-Modul: Prüfung, Zitatabgleich, Prompt, KI-Pipeline mit Fake-LLM."""

import json

from app.protokoll import LLMClient, bereinigt, build_prompt, check_quote, create_protokoll, verify
from app.store import Segment, Transcript


def make_transcript():
    t = Transcript(id="t1", title="Ausschuss", created_at="2026-09-26T10:00:00+00:00", source="upload", status="done", duration=90)
    t.segments = [
        Segment(0, 0.0, 4.0, "Guten Tag, wir beginnen mit der Sitzung.", "Vorsitz"),
        Segment(1, 5.0, 9.0, "Der erste Tagesordnungspunkt ist der Haushalt 2027.", "Vorsitz"),
        Segment(2, 10.0, 14.0, "Wir beschließen, den Haushaltsentwurf an den Gemeinderat weiterzuleiten.", "Vorsitz"),
        Segment(3, 15.0, 18.0, "Frau Müller übernimmt die Nachfrage bei der Kämmerei bis Freitag.", "Vorsitz"),
    ]
    return t


def test_verify_counts():
    t = make_transcript()
    md = "## Themen\n- Haushalt 2027 vorgestellt. [S1]\n- Etwas ohne Beleg.\n- Beleg auf falsches Segment. [S7]\n- Unverständlich [unklar]\n## Beschlüsse\nkeine"
    p = verify(t, md)
    assert p["zeilen_inhalt"] == 4
    assert p["unbelegt"] == 1
    assert p["ungueltige_belege"] == 1
    assert [z["status"] for z in p["zeilen"] if z["text"].strip() and not z["heading"] and z["text"].strip() != "keine"] == [
        "ok", "unbelegt", "ungueltig", "unklar"]


def test_verify_multi_refs():
    t = make_transcript()
    p = verify(t, "- Beschluss mit zwei Belegen. [S2, S3]\n- Anders geschrieben [S 1; S3]")
    assert [z["refs"] for z in p["zeilen"]] == [[2, 3], [1, 3]]
    assert p["unbelegt"] == 0 and p["ungueltige_belege"] == 0


def test_check_quote():
    seg = "Wir beschließen, den Haushaltsentwurf an den Gemeinderat weiterzuleiten."
    assert check_quote("den Haushaltsentwurf an den Gemeinderat", seg) == 1.0
    assert check_quote("Haushaltsentwurf an Gemeinderat weiterleiten", seg) > 0.6
    assert check_quote("Die Sitzung wird vertagt.", seg) < 0.6


def test_prompt_contains_rules_and_segments():
    p = build_prompt(make_transcript(), "verlauf")
    assert "Verlaufsprotokoll" in p and "[S3 | 00:15 | Vorsitz]" in p and "[unklar]" in p


class FakeLLM(LLMClient):
    def __init__(self):
        super().__init__("http://fake", "", "fake-model")
        self.calls = 0

    def chat(self, system, user, temperature=0.0, json_mode=False):
        self.calls += 1
        if json_mode:
            return json.dumps({
                "themen": ["Haushalt 2027"],
                "bausteine": [
                    {"id": "B1", "typ": "information", "thema": "Haushalt", "text": "Der Haushalt 2027 ist erster TOP.",
                     "sprecher": "Vorsitz", "segment": 1, "zitat": "erste Tagesordnungspunkt ist der Haushalt 2027"},
                    {"id": "B2", "typ": "beschluss", "thema": "Haushalt", "text": "Entwurf wird an den Gemeinderat weitergeleitet.",
                     "sprecher": "Vorsitz", "segment": 2, "zitat": "den Haushaltsentwurf an den Gemeinderat weiterzuleiten"},
                    {"id": "B3", "typ": "aufgabe", "thema": "Haushalt", "text": "Erfundene Aufgabe.",
                     "sprecher": "", "segment": 3, "zitat": "Dieser Satz kommt im Transkript nicht vor, er ist erfunden."},
                ]})
        assert '"B3"' not in user, "verworfener Baustein darf nicht an Stufe 2 gehen"
        return "## Ergebnisse\n- Der Haushalt 2027 ist erster TOP. [S1]\n## Beschlüsse\n- Entwurf wird an den Gemeinderat weitergeleitet. [S2]\n## Aufgaben\nkeine"


def test_create_protokoll_pipeline():
    llm = FakeLLM()
    c = create_protokoll(make_transcript(), "ergebnis", llm)
    assert llm.calls == 2
    assert c["bausteine_verworfen"] == 1
    assert [b["beleg_ok"] for b in c["bausteine"]] == [True, True, False]
    assert c["pruefung"]["unbelegt"] == 0 and c["pruefung"]["quote_belegt"] == 1.0
    assert "[S2]" in c["protokoll_md"]


def test_bereinigt():
    assert bereinigt("ähm also wir, äh, beschließen das. Hm, genau.") == "Also wir, beschließen das. Genau."
