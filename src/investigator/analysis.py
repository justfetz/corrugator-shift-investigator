"""Safe bounded demo XML ingestion and parameterized analysis tools."""
from datetime import datetime, timedelta
import hashlib
import json
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
        self._orders = {}
        self._db = duckdb.connect(":memory:", config={"threads": 2})
        self._db.execute("CREATE TABLE shifts (shift INTEGER, scheduled_seconds DOUBLE, notes VARCHAR)")
        self._db.execute("CREATE TABLE setups (id VARCHAR, shift INTEGER, wet_end_id VARCHAR, grade VARCHAR, target DOUBLE, seconds DOUBLE, feet DOUBLE, gross DOUBLE, trim DOUBLE, shear DOUBLE, rejects DOUBLE, reject_reason VARCHAR, started TIMESTAMP, ended TIMESTAMP, width_in DOUBLE)")
        self._db.execute("CREATE TABLE stops (id VARCHAR, shift INTEGER, kind VARCHAR, place VARCHAR, reason VARCHAR, seconds DOUBLE, started TIMESTAMP, ended TIMESTAMP, notes VARCHAR)")
        self._setup_rows = []
        self._order_rows = []
        self._db.execute("CREATE TABLE orders (record_id VARCHAR, shift INTEGER, setup_id VARCHAR, order_id VARCHAR, grade VARCHAR, knife VARCHAR, width_in DOUBLE, length_in DOUBLE, outs INTEGER, cuts INTEGER, requested_sheets INTEGER, planned_sheets INTEGER, produced_sheets INTEGER, remaining_before INTEGER, remaining_after INTEGER)")
        stop_rows, shift_rows = [], []
        seen = set()
        shifts = list(root)
        if len(shifts) != 3 or any(s.tag != "shift" for s in shifts):
            raise ValueError("Expected exactly three shifts")
        for i, shift in enumerate(shifts, 1):
            begin, finish, seconds = duration(shift)
            if shift.get("number") != str(i) or begin != origin+timedelta(hours=8*(i-1)) or seconds != 28800:
                raise ValueError("Invalid shift calendar")
            shift_rows.append([i, seconds, self._note(shift)])
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
                    stop_rows.append([ident, i, node.get("kind"), node.attrib["place"], node.attrib["reason"], elapsed, start, end, self._note(node)])
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
        for table, rows in (("shifts", shift_rows), ("setups", self._setup_rows), ("stops", stop_rows), ("orders", self._order_rows)):
            if rows:
                schema = self._db.execute(f"DESCRIBE {table}").fetchall()
                records = [dict(zip([r[0] for r in schema], row)) for row in rows]
                # One JSON scalar avoids per-value optional-module probes in the driver.
                # Names/types come only from the fixed schema, never source/user text.
                fields = ", ".join(f"CAST(value->>'{name}' AS {kind})" for name, kind, *_ in schema)
                self._db.execute(f"INSERT INTO {table} SELECT {fields} FROM json_each(?)",
                                 [json.dumps(records, default=lambda value: value.isoformat())])
        del self._setup_rows
        del self._order_rows
        self._db.execute("SET enable_external_access=false")

    @staticmethod
    def _note(node):
        note = node.get("notes", "")
        if len(note) > 1000:
            raise ValueError("Notes exceed 1000 characters")
        return note

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
        if not (len(knives) == 1 and knives[0].get("level") == "upper" or
                len(knives) == 2 and {k.get("level") for k in knives} == {"upper", "lower"}):
            raise ValueError("Expected one or two knife assignments")
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
        if (any(not math.isclose(a[1], b[0]) for a,b in zip(spans,spans[1:])) or
                not math.isclose(spans[0][0], width-spans[-1][1]) or spans[-1][1] > width):
            raise ValueError("Invalid centered web placement")
        if any(not math.isclose(travels[0], value) for value in travels) or not math.isclose(feet, travels[0]+shear*12/width):
            raise ValueError("Knife footage does not reconcile")
        if not math.isclose(trim, travels[0]*(width-sum(b-a for a,b in spans))/12):
            raise ValueError("Trim geometry mismatch")
        upper = next(k for k in knives if k.get("level") == "upper")
        sheets = number(node, "reject_sheets")
        if not sheets.is_integer() or sheets > number(upper, "cuts")*number(upper, "outs") or not math.isclose(rejects, sheets*number(upper, "width_in")*number(upper, "length_in")/144):
            raise ValueError("Reject sheets do not reconcile")
        from .fixture import REJECT_REASONS
        if node.get("reject_reason") not in (*REJECT_REASONS, "Warp", "Bond"):
            raise ValueError("Unsupported reject reason")
        orders = {}
        for knife in knives:
            if "order_quantity" not in knife.attrib:
                continue  # Older demo XML has no order ledger.
            requested, planned, produced, before, after = [number(knife, k) for k in
                ("order_quantity", "planned_sheets", "produced_sheets", "remaining_before", "remaining_after")]
            if (any(not v.is_integer() for v in (requested,planned,produced,before,after)) or
                    requested <= 0 or planned < requested or before > planned or
                    produced != number(knife,"cuts")*number(knife,"outs") or before-produced != after):
                raise ValueError("Order quantities do not reconcile")
            identity = (node.get("grade"), number(knife,"width_in"), number(knife,"length_in"), requested, planned)
            key = knife.attrib["order_id"]
            if key in self._orders and self._orders[key] != (identity,before):
                raise ValueError("Order identity or remaining quantity changed")
            orders[key] = (identity,after)
        group_key = (shift, node.attrib["wet_end_id"])
        group_value = (node.attrib["grade"], width, target)
        if group_key in self._wet_ends and self._wet_ends[group_key] != group_value:
            raise ValueError("Wet-end group changes grade, width or target")
        self._wet_ends[group_key] = group_value
        self._orders.update(orders)
        for knife in knives:
            if 'order_quantity' in knife.attrib:
                self._order_rows.append([node.attrib['id'],shift,node.get('setup_id',node.attrib['id']),
                    knife.attrib['order_id'],node.attrib['grade'],knife.attrib['level'],
                    *[number(knife,k) for k in ('width_in','length_in','outs','cuts','order_quantity',
                        'planned_sheets','produced_sheets','remaining_before','remaining_after')]])
        self._setup_rows.append([node.attrib["id"], shift, node.attrib["wet_end_id"], node.attrib["grade"], target, elapsed, feet, gross, trim, shear, rejects, node.attrib["reject_reason"], datetime.fromisoformat(node.attrib["start"]), datetime.fromisoformat(node.attrib["end"]), width])

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
        return self._query(shift, """SELECT count(*) AS valid_setups, count(DISTINCT wet_end_id) AS paper_changes,
        sum(feet)/nullif(sum(target*seconds/60),0)*100 AS speed_to_target_pct,
        sum(feet) AS observed_lineal_ft, sum(feet)/480 AS observed_shift_fpm,
        sum(gross) AS gross_sqft, sum(gross-trim-shear-rejects) AS estimated_good_sqft,
        sum(gross)/nullif(sum(feet),0)*12 AS throughput_in,
        sum(trim)/nullif(sum(gross),0)*100 AS trim_pct,
        sum(shear)/nullif(sum(gross),0)*100 AS shear_pct,
        sum(rejects)/nullif(sum(gross),0)*100 AS dry_end_pct,
        (SELECT coalesce(sum(seconds),0)/28800*100 FROM stops WHERE shift = ? AND kind = 'Maintenance') AS maintenance_pct,
        (SELECT coalesce(sum(seconds),0)/28800*100 FROM stops WHERE shift = ? AND kind = 'Operator') AS operator_pct,
        (SELECT coalesce(sum(seconds),0)/28800*100 FROM stops WHERE shift = ?) AS downtime_pct,
        sum(feet)/nullif(count(*),0) AS lineal_per_setup,
        sum(feet)/nullif(count(DISTINCT wet_end_id),0) AS lineal_per_wet_end,
        count(*) FILTER (WHERE trim/gross*100 > 3.25) AS setups_above_trim_target,
        list(id ORDER BY id) AS source_ids FROM setups WHERE shift = ?""", [shift, shift, shift, shift])

    def get_shift_overview(self, shift):
        """One bounded read bundle for a narrative covering speed, stops and waste."""
        data=self.get_shift_kpis(shift)
        components=[self.get_downtime_breakdown(shift), self.get_wet_end_performance(shift),
                    self.get_quality_breakdown(shift)]
        row=data['rows'][0]
        stops,speed,quality=(c['rows'] for c in components)
        row.update({'largest_downtime_reason':stops[0]['category'] if stops else 'No recorded stops',
                    'largest_downtime_minutes':stops[0]['down_minutes'] if stops else 0,
                    'largest_reject_reason':quality[0]['reason'] if quality else 'No recorded rejects',
                    'largest_reject_sqft':quality[0]['rejected_sqft'] if quality else 0,
                    'wet_end_runs_below_target':sum(r['actual_fpm']<r['target_fpm'] for r in speed),
                    'wet_end_runs_observed':len(speed)})
        data['components']=components
        return data

    def get_order_matrix(self, shift):
        return self._query(shift, """SELECT *, planned_sheets-requested_sheets AS planned_overrun_sheets,
        [record_id] AS source_ids FROM orders WHERE shift = ? ORDER BY record_id, knife""", [shift])

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
        sum(seconds)/60 AS elapsed_minutes,
        sum(feet)/nullif(sum(target*seconds/60),0)*100 AS speed_to_target_pct, list(id ORDER BY id) AS source_ids
        FROM setups WHERE shift = ? GROUP BY wet_end_id, grade ORDER BY wet_end_id""", [shift])

    def get_quality_breakdown(self, shift):
        return self._query(shift, """SELECT reject_reason AS reason, sum(rejects) AS rejected_sqft,
        list(id ORDER BY id) AS source_ids FROM setups WHERE shift = ?
        GROUP BY reject_reason ORDER BY rejected_sqft DESC, reason""", [shift])

    def get_setup_matrix(self, shift):
        return self._query(shift, """SELECT s.id AS setup_id, s.wet_end_id, s.grade,
        strftime(s.started, '%Y-%m-%d %H:%M:%S') AS start,
        strftime(s.ended, '%Y-%m-%d %H:%M:%S') AS end,
        s.width_in, s.feet AS lineal_ft, s.feet/(s.seconds/60) AS actual_fpm,
        s.target AS target_fpm, s.feet/(s.seconds/60)/s.target*100 AS speed_to_target_pct,
        count(d.id) AS stop_count,
        coalesce(sum(CASE WHEN d.id IS NOT NULL THEN epoch(least(s.ended,d.ended)-greatest(s.started,d.started)) ELSE 0 END),0)/60 AS down_minutes,
        s.rejects AS rejected_sqft, [s.id] AS source_ids
        FROM setups s LEFT JOIN stops d ON s.shift=d.shift AND d.started<s.ended AND d.ended>s.started
        WHERE s.shift=? GROUP BY ALL ORDER BY start""", [shift])

    def get_shift_notes(self, shift):
        return self._query(shift, """SELECT 'Shift' AS record_type, 'Shift ' || shift AS record_id,
        '' AS start, '' AS end, '' AS kind, '' AS place, '' AS reason, NULL AS down_minutes,
        notes, ['shift-' || shift] AS source_ids FROM shifts WHERE shift=?
        UNION ALL SELECT 'Downtime', id, strftime(started,'%Y-%m-%d %H:%M:%S'),
        strftime(ended,'%Y-%m-%d %H:%M:%S'), kind, place, reason, seconds/60, notes, [id]
        FROM stops WHERE shift=? ORDER BY record_type DESC, start, record_id""", [shift, shift])

    def get_rankings(self, shift, metric='speed'):
        queries = {
            'speed': """WITH runs AS (SELECT wet_end_id AS item, grade,
                sum(feet)/sum(target*seconds/60)*100 AS value, list(id ORDER BY id) AS source_ids
                FROM setups WHERE shift=? GROUP BY wet_end_id, grade)
                SELECT dense_rank() OVER (ORDER BY value) AS rank, *, '% of target (lowest first)' AS unit
                FROM runs ORDER BY value, item LIMIT 10""",
            'downtime': """WITH losses AS (SELECT place || ' / ' || reason AS item,
                sum(seconds)/60 AS value, list(id ORDER BY id) AS source_ids
                FROM stops WHERE shift=? GROUP BY place, reason)
                SELECT dense_rank() OVER (ORDER BY value DESC) AS rank, *, 'minutes (highest first)' AS unit
                FROM losses ORDER BY value DESC, item LIMIT 10""",
            'quality': """WITH losses AS (SELECT reject_reason AS item, sum(rejects) AS value,
                list(id ORDER BY id) AS source_ids FROM setups WHERE shift=? GROUP BY reject_reason)
                SELECT dense_rank() OVER (ORDER BY value DESC) AS rank, *, 'sq ft (highest first)' AS unit
                FROM losses ORDER BY value DESC, item LIMIT 10"""}
        if metric not in queries:
            raise ValueError('Unsupported ranking metric')
        result = self._query(shift, queries[metric], [shift])
        result['metric'] = metric
        if result['coverage'] != 'complete':
            result['rows'] = []
        return result

    def close(self):
        self._db.close()
