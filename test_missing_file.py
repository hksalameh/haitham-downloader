"""Regression: completed entry whose file moved/was deleted is not shown as complete."""
import tempfile
from pathlib import Path
from ranges import RangeManager

with tempfile.TemporaryDirectory(prefix="haitham_missing_") as tmp:
    base=Path(tmp)
    m=RangeManager(base/"state")
    target=base/"completed.bin"
    gid="seg-test-missing"
    m.records[gid]={"gid":gid, "name":"completed.bin", "url":"http://localhost/no-network",
        "filepath":str(target), "directory":str(base), "date":"2026-10-26 21:00",
        "status":"complete", "total":4, "ranges":[{"index":1,"start":0,"end":3,"size":4}], "error":""}
    assert m.get(gid)["status"]=="missing", m.get(gid)
    assert m.get(gid)["completed"]==0, m.get(gid)
    assert m.records[gid]["status"]=="complete", "History should not be rewritten"
    target.write_bytes(b"ok")
    assert m.get(gid)["status"]=="missing"
    target.write_bytes(b"okay")
    assert m.get(gid)["status"]=="complete"
    assert m.get(gid)["completed"]==4
print("PASS_MISSING_FILE_STATUS_AND_RECOVERY")
