"""Safe bounded demo XML ingestion and parameterized analysis tools."""
from datetime import datetime, timedelta
import hashlib
import math
import time
from threading import Timer
import xml.etree.ElementTree as ET
from xml.parsers import expat
import duckdb

MAX_BYTES = 1_000_000


def safe_xml(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("XML must be bytes within the 1 MB limit")
    text = data.decode("utf-8", errors="strict")
    if "\x00" in text:
        raise ValueError("Only UTF-8 XML is supported")
    parser = expat.ParserCreate()
    depth = count = 0
    def reject(*args):
        raise ValueError("DTD and entity declarations are forbidden")
    def start(*args):
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > 8 or count > 3000:
            raise ValueError("XML structure exceeds limits")
    def end(*args):
        nonlocal depth
        depth -= 1
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(text, True)
    return ET.fromstring(text)


def number(node, key):
    value = float(node.attrib[key])
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {key}")
    return value


def duration(node):
    start, end = (datetime.fromisoformat(node.attrib[k]) for k in ("start", "end"))
    if start.tzinfo or end.tzinfo or end <= start:
        raise ValueError("Invalid demo-local interval")
    return start, end, (end-start).total_seconds()


class Analysis:
    """Private in-memory database: only fixed read queries are exposed as tools."""
    def __init__(self, data):
        root = safe_xml(data)
        if root.tag != "production" or root.get("synthetic") != "true" or root.get("version") != "1" or root.get("clock") != "fixed-demo-local":
            raise ValueError("Unsupported demo schema")
        self.day = root.attrib["production_day"]
        origin = datetime.fromisoformat(self.day + "T07:00:00")
        self.version = hashlib.sha256(data).hexdigest()
        self.errors = []
        self._wet_ends = {}
        self._db = duckdb.connect(":memory:")
        self._db.execute("CREATE TABLE shifts (shift INTEGER, scheduled_seconds DOUBLE)")
        self._db.execute("CREATE TABLE setups (id VARCHAR, shift INTEGER, wet_end_id VARCHAR, grade VARCHAR, target DOUBLE, seconds DOUBLE, feet DOUBLE, gross DOUBLE, trim DOUBLE, shear DOUBLE, rejects DOUBLE, reject_reason VARCHAR)")
        self._db.execute("CREATE TABLE stops (id VARCHAR, shift INTEGER, kind VARCHAR, place VARCHAR, reason VARCHAR, seconds DOUBLE)")
        seen = set()
        shifts = list(root)
        if len(shifts) != 3 or any(s.tag != "shift" for s in shifts):
            raise ValueError("Expected exactly three shifts")
        for i, shift in enumerate(shifts, 1):
            begin, finish, seconds = duration(shift)
            if shift.get("number") != str(i) or begin != origin+timedelta(hours=8*(i-1)) or seconds != 28800:
                raise ValueError("Invalid shift calendar")
            self._db.execute("INSERT INTO shifts VALUES (?, ?)", [i, seconds])
            prior_setup = begin
            intervals = []
            for node in shift:
                ident = node.attrib["id"]
                if ident in seen:
                    raise ValueError("Duplicate record ID")
                seen.add(ident)
                start, end, elapsed = duration(node)
                if start < begin or end > finish:
                    raise ValueError("Cross-shift records require allocation; unsupported in fixture v1")
                if node.tag == "stop":
                    if node.get("kind") not in ("Maintenance", "Operator"):
                        raise ValueError("Unsupported downtime kind")
                    intervals.append((start, end))
                    self._db.execute("INSERT INTO stops VALUES (?, ?, ?, ?, ?, ?)",
                        [ident, i, node.get("kind"), node.attrib["place"], node.attrib["reason"], elapsed])
                elif node.tag == "setup":
                    if start != prior_setup:
                        raise ValueError("Setup timeline must partition shift")
                    prior_setup = end
                    try:
                        self._setup(node, i, elapsed)
                    except (ValueError, KeyError) as exc:
                        self.errors.append({"record_id": ident, "shift": i, "error": str(exc), "raw": dict(node.attrib)})
                else:
                    raise ValueError("Unknown record type")
            if prior_setup != finish:
                raise ValueError("Incomplete setup timeline")
            intervals.sort()
            if any(b[0] < a[1] for a, b in zip(intervals, intervals[1:])):
                raise ValueError("Overlapping stops require resolution; unsupported in fixture v1")
        self._db.execute("SET enable_external_access=false")

    def _setup(self, node, shift, elapsed):
        from .fixture import GRADES
        width, feet, gross, trim, shear, rejects, target = [number(node, k) for k in
            ("width_in", "gross_feet", "gross_sqft", "trim_sqft", "shear_sqft", "reject_sqft", "target_fpm")]
        if width not in (98, 95, 92, 87) or feet <= 0 or gross <= 0:
            raise ValueError("Invalid gross geometry")
        if node.get("grade") not in GRADES or target != GRADES[node.get("grade")]:
            raise ValueError("Invalid demo grade target")
        if not math.isclose(gross, feet*width/12) or trim+shear+rejects > gross:
            raise ValueError("Waste/gross area out of bounds")
        knives = list(node)
        if len(knives) != 2 or {k.get("level") for k in knives} != {"upper", "lower"}:
            raise ValueError("Expected two knife assignments")
        spans, travels = [], []
        for knife in knives:
            if knife.tag != "knife" or knife.get("stacker") != knife.get("level"):
                raise ValueError("Invalid knife routing")
            outs, cuts, w, length, left = [number(knife, k) for k in ("outs", "cuts", "width_in", "length_in", "start_in")]
            if not outs.is_integer() or not cuts.is_integer() or min(outs, cuts, w, length) <= 0:
                raise ValueError("Invalid knife quantities")
            spans.append((left, left+outs*w))
            travels.append(cuts*length/12)
        spans.sort()
        if spans[0][1] > spans[1][0] or not math.isclose(spans[0][1], spans[1][0]) or not math.isclose(spans[0][0], width-spans[1][1]) or spans[1][1] > width:
            raise ValueError("Invalid centered web placement")
        if not math.isclose(travels[0], travels[1]) or not math.isclose(feet, travels[0]+shear*12/width):
            raise ValueError("Knife footage does not reconcile")
        if not math.isclose(trim, travels[0]*(width-sum(b-a for a,b in spans))/12):
            raise ValueError("Trim geometry mismatch")
        upper = next(k for k in knives if k.get("level") == "upper")
        sheets = number(node, "reject_sheets")
        if not sheets.is_integer() or sheets > number(upper, "cuts")*number(upper, "outs") or not math.isclose(rejects, sheets*number(upper, "width_in")*number(upper, "length_in")/144):
            raise ValueError("Reject sheets do not reconcile")
        if node.get("reject_reason") not in ("Warp", "Bond", "Misalignment"):
            raise ValueError("Unsupported reject reason")
        group_key = (shift, node.attrib["wet_end_id"])
        group_value = (node.attrib["grade"], width, target)
        if group_key in self._wet_ends and self._wet_ends[group_key] != group_value:
            raise ValueError("Wet-end group changes grade, width or target")
        self._wet_ends[group_key] = group_value
        self._db.execute("INSERT INTO setups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [node.attrib["id"], shift, node.attrib["wet_end_id"], node.attrib["grade"], target, elapsed, feet, gross, trim, shear, rejects, node.attrib["reject_reason"]])

    def _query(self, shift, sql, params):
        if type(shift) is not int or shift not in (1, 2, 3):
            raise ValueError("Shift must be 1, 2 or 3")
        start = time.perf_counter()
        timer = Timer(2.0, self._db.interrupt)
        timer.daemon = True
        timer.start()
        try:
            result = self._db.execute(sql, params)
            columns = [c[0] for c in result.description]
            fetched = result.fetchmany(1001)
            if len(fetched) > 1000:
                raise ValueError("Result exceeds row limit")
            rows = [dict(zip(columns, row)) for row in fetched]
        except duckdb.InterruptException as exc:
            raise ValueError("Query exceeded two-second limit") from exc
        finally:
            timer.cancel()
            timer.join()
        errors = [e for e in self.errors if e["shift"] == shift]
        return {"production_day": self.day, "shift": shift, "dataset_version": self.version,
                "rows": rows, "coverage": "incomplete" if errors else "complete",
                "excluded_records": errors, "trace": {"sql": sql, "parameters": params,
                "elapsed_ms": (time.perf_counter()-start)*1000}}

    def get_shift_kpis(self, shift):
        return self._query(shift, """SELECT count(*) AS valid_setups,
        sum(feet) AS observed_lineal_ft, sum(feet)/480 AS observed_shift_fpm,
        sum(gross) AS gross_sqft, sum(gross)/nullif(sum(feet),0)*12 AS throughput_in,
        sum(trim)/nullif(sum(gross),0)*100 AS trim_pct,
        sum(shear)/nullif(sum(gross),0)*100 AS shear_pct,
        sum(rejects)/nullif(sum(gross),0)*100 AS dry_end_pct,
        (SELECT coalesce(sum(seconds),0)/28800*100 FROM stops WHERE shift = ? AND kind = 'Maintenance') AS maintenance_pct,
        (SELECT coalesce(sum(seconds),0)/28800*100 FROM stops WHERE shift = ? AND kind = 'Operator') AS operator_pct,
        sum(feet)/nullif(count(*),0) AS lineal_per_setup,
        sum(feet)/nullif(count(DISTINCT wet_end_id),0) AS lineal_per_wet_end,
        count(*) FILTER (WHERE trim/gross*100 > 3.25) AS setups_above_trim_target,
        list(id ORDER BY id) AS source_ids FROM setups WHERE shift = ?""", [shift, shift, shift])

    def get_downtime_breakdown(self, shift, kind=None, group_by="reason"):
        if group_by not in ("reason", "kind", "place") or kind not in (None, "Maintenance", "Operator"):
            raise ValueError("Unsupported filter or grouping")
        # group_by is strictly allowlisted; all visitor values are bound.
        sql = f"""SELECT {group_by} AS category, sum(seconds)/60 AS down_minutes,
        sum(seconds)/28800*100 AS shift_percent, list(id ORDER BY id) AS source_ids
        FROM stops WHERE shift = ?"""
        params = [shift]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += f" GROUP BY {group_by} ORDER BY down_minutes DESC, category"
        return self._query(shift, sql, params)

    def get_wet_end_performance(self, shift):
        return self._query(shift, """SELECT wet_end_id, grade, max(target) AS target_fpm,
        sum(feet)/(sum(seconds)/60) AS actual_fpm, sum(feet) AS lineal_ft,
        sum(seconds)/60 AS elapsed_minutes, list(id ORDER BY id) AS source_ids
        FROM setups WHERE shift = ? GROUP BY wet_end_id, grade ORDER BY wet_end_id""", [shift])

    def get_quality_breakdown(self, shift):
        return self._query(shift, """SELECT reject_reason AS reason, sum(rejects) AS rejected_sqft,
        list(id ORDER BY id) AS source_ids FROM setups WHERE shift = ?
        GROUP BY reject_reason ORDER BY rejected_sqft DESC, reason""", [shift])

    def close(self):
        self._db.close()
