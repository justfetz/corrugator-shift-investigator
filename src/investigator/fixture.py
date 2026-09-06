"""Small, reproducible fixture. Values are simulation choices, not plant standards."""
from datetime import datetime, timedelta
import random
import xml.etree.ElementTree as ET

GRADES = {"26-C": 900, "32-C": 900, "55-C": 850, "200-C": 800,
          "275-BC": 500, "275-EB": 500}


def generate(seed=7, production_day="2026-09-01", corrupt=False):
    rng = random.Random(seed)
    quality_rng = random.Random(seed + 10000)
    root = ET.Element("production", version="1", synthetic="true",
                      production_day=production_day, clock="fixed-demo-local")
    origin = datetime.fromisoformat(production_day + "T07:00:00")
    for shift in range(1, 4):
        begin = origin + timedelta(hours=8*(shift-1))
        node = ET.SubElement(root, "shift", number=str(shift),
                             start=begin.isoformat(), end=(begin+timedelta(hours=8)).isoformat(),
                             notes=("Cool, damp weather reported at shift start; watch bond quality.",
                                    "Crew reported difficulty during the BC flute transition. Knife jams recorded separately.",
                                    "Safety incident reported near the stacker walkway; supervisor notified. Details require review.")[shift-1])
        for n in range(60):
            start = begin + timedelta(minutes=8*n)
            # Identical grade mix across shifts, two setups per wet-end run.
            grade = list(GRADES)[(n//2) % 6]
            width = (98, 95, 92, 87)[(n//2) % 4]
            stop_seconds = (240 if shift == 2 else 60) if n in (8, 18, 28, 38, 48) else 0
            if stop_seconds and n in (8, 18, 28):
                stop_seconds += min(3, max(0, (seed-7)//7))*30
            target = GRADES[grade]
            factor = rng.uniform(.84, .89) if shift != 2 else rng.uniform(.66, .73)
            # Net knife travel is an integer multiple of both 45 and 30 inches.
            net_feet = int(target * ((480-stop_seconds)/60) * factor / 7.5) * 7.5
            chops = 6
            gross_feet = net_feet + chops*34/12
            gross_area = gross_feet*width/12
            trim_area = net_feet*2/12
            reject_reason = quality_rng.choices(("Warp", "Bond", "Misalignment"), weights=(5, 3, 2))[0]
            rejects = quality_rng.randint(5, 17) if shift != 3 else quality_rng.randint(30, 85)
            if reject_reason == "Warp":
                rejects += quality_rng.randint(4, 12)
            reject_area = rejects*30*45/144
            setup = ET.SubElement(node, "setup", id=f"S{shift}-{n:02}", wet_end_id=f"W{shift}-{n//2:02}",
                grade=grade, target_fpm=str(target), start=start.isoformat(),
                end=(start+timedelta(minutes=8)).isoformat(), width_in=str(width),
                gross_feet=str(gross_feet), gross_sqft=str(gross_area),
                trim_sqft=str(99999999 if corrupt and shift == 2 and n == 0 else trim_area), shear_sqft=str(chops*34*width/144),
                reject_sheets=str(rejects), reject_sqft=str(reject_area),
                reject_reason=reject_reason)
            ET.SubElement(setup, "knife", level="upper", stacker="upper", order_id=f"A{shift}-{n//2}",
                start_in="1", outs="2", width_in="30", length_in="45", cuts=str(int(net_feet*12/45)))
            ET.SubElement(setup, "knife", level="lower", stacker="lower", order_id=f"B{shift}-{n}",
                start_in="61", outs="1", width_in=str(width-62), length_in="30", cuts=str(int(net_feet*12/30)))
            if stop_seconds:
                stop = start + timedelta(seconds=30)
                ET.SubElement(node, "stop", id=f"D{shift}-{n}", start=stop.isoformat(),
                    end=(stop+timedelta(seconds=stop_seconds)).isoformat(),
                    kind="Maintenance" if n != 48 else "Operator",
                    place=("Upper knife" if n in (8, 18, 28) else "Lower knife") if n != 48 else "Wet end",
                    reason="Knife jam" if n != 48 else "Missed splice",
                    notes=("Sheets jammed at knife discharge; cleared material and checked alignment." if n != 48
                           else "Missed splice at wet end; crew rethreaded and resumed. Cause not confirmed."))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
