# Integration test: needs the MySQL container. Guards that results files record the
# environment CLAUDE.md rule 7 requires, read live from the server.
from app.db import get_readonly_conn
from app.eval.runinfo import run_metadata


def test_run_metadata_records_live_server_and_hardware():
    with get_readonly_conn() as conn:
        m = run_metadata(conn)
    assert m["mysql"]["version"] == "8.4.13"
    assert m["mysql"]["innodb_buffer_pool_size"] == 3 * 1024**3  # the flag really took effect
    assert m["mysql"]["collation_server"] == "utf8mb4_0900_ai_ci"
    assert m["mysql"]["local_infile"] == 0  # only ever on during the TPC-H load
    assert m["query_timeout_s"] == 120
    hw = m["hardware"]
    assert hw["cpu"] and hw["logical_cores"] and hw["host_ram_bytes"]
