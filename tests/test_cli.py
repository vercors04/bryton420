from bryton_route import bryton, cli

from conftest import REFERENCE_FIT, REFERENCE_TRACKS, data


def test_convert_writes_the_route_into_a_plantrip_folder(tmp_path, capsys):
    gpx = tmp_path / "Sortie à Lyon.gpx"
    gpx.write_bytes(data("brouter_osmand.gpx"))
    plantrip = tmp_path / "PlanTrip"
    plantrip.mkdir()

    assert cli.main(["convert", str(gpx), "-o", str(plantrip)]) == 0

    route, summary = bryton.decode((plantrip / "Sortie à Lyon.fit").read_bytes())
    assert len(route.cues) == 61 and summary["crc_ok"]
    assert "PlanTrip" in capsys.readouterr().out


def test_convert_defaults_to_a_fit_next_to_the_gpx(tmp_path):
    gpx = tmp_path / "boucle.gpx"
    gpx.write_bytes(data("plotaroute_right_turn.gpx"))
    assert cli.main(["convert", str(gpx)]) == 0
    assert (tmp_path / "boucle.fit").exists()


def test_convert_names_the_route_on_request(tmp_path):
    gpx = tmp_path / "brouter_export (3).gpx"
    gpx.write_bytes(data("plotaroute_right_turn.gpx"))
    assert cli.main(["convert", str(gpx), "--name", "Tour du lac: Annecy"]) == 0
    assert (tmp_path / "Tour du lac- Annecy.fit").exists()

    plantrip = tmp_path / "PlanTrip"
    plantrip.mkdir()
    assert cli.main(["convert", str(gpx), "-o", str(plantrip), "--name", "Annecy"]) == 0
    assert (plantrip / "Annecy.fit").exists()


def test_doctor_lists_cues_and_writes_nothing(tmp_path, capsys):
    gpx = tmp_path / "fr.gpx"
    gpx.write_bytes(data("plotaroute_route_fr.gpx"))
    assert cli.main(["doctor", str(gpx)]) == 0
    out = capsys.readouterr().out
    assert "Rue Étienne Dolet" in out and "rester à droite" in out
    assert list(tmp_path.iterdir()) == [gpx]


def test_offline_doctor_explains_a_gpx_without_instructions(tmp_path, capsys):
    gpx = tmp_path / "strava.gpx"
    doc = data("brouter_osmand.gpx")
    gpx.write_bytes(doc[: doc.index(b"<rte>")] + doc[doc.index(b"</rte>") + 6 :])
    assert cli.main(["doctor", str(gpx), "--offline"]) == 2
    assert "turnInstructionMode" in capsys.readouterr().err


def test_inspect_reads_app_and_device_files(capsys):
    assert cli.main(["inspect", str(REFERENCE_FIT), "--cues"]) == 0
    out = capsys.readouterr().out
    assert "96" in out and "Via Ardèche" in out
    assert cli.main(["inspect", str(REFERENCE_TRACKS.with_suffix(".tinfo"))]) == 0
    assert "96 instructions" in capsys.readouterr().out


def test_a_missing_file_is_reported_in_french(tmp_path, capsys):
    assert cli.main(["convert", str(tmp_path / "absent.gpx")]) == 1
    assert "fichier introuvable" in capsys.readouterr().err


def test_rider_reports_what_the_device_imported(tmp_path, capsys):
    assert cli.main(["rider", str(REFERENCE_FIT.parent.parent)]) == 0
    out = capsys.readouterr().out
    assert "De Uzer à Soyons.fit : 81,27 km, 96 instructions" in out
    assert "importé : 96 instructions, 1842 points, identiques au .fit" in out

    plantrip = tmp_path / "PlanTrip"
    plantrip.mkdir()
    gpx = tmp_path / "boucle.gpx"
    gpx.write_bytes(data("plotaroute_right_turn.gpx"))
    assert cli.main(["convert", str(gpx), "-o", str(plantrip)]) == 0
    capsys.readouterr()
    assert cli.main(["rider", str(tmp_path)]) == 0
    assert "pas encore importé" in capsys.readouterr().out

    assert cli.main(["rider", str(tmp_path / "PlanTrip")]) == 1
    assert "pas de dossier PlanTrip" in capsys.readouterr().err
