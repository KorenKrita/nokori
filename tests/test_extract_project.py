
from nokori.config import Config
from nokori.extract import jobs as job_io


def test_find_project_id_from_job(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    transcript = tmp_path / "proj-a.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    mtime = transcript.stat().st_mtime
    job_io.write_job(cfg, transcript, "myproj-abc12345", mtime)
    pid = job_io.find_project_id_for_transcript(cfg, transcript)
    assert pid == "myproj-abc12345"
