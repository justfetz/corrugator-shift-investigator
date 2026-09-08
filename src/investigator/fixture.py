"""Quantity-driven synthetic orders; simulation settings, not plant standards."""
from datetime import datetime, timedelta
from math import ceil, floor, lcm
import random
import xml.etree.ElementTree as ET

GRADES = {"26-C": 900, "32-C": 900, "55-C": 850, "200-C": 800,
          "275-BC": 500, "275-EB": 500}
REJECT_REASONS = ("Up warp", "Down warp", "Bond - delamination",
                  "Bond - paper/raw-material issue", "Misalignment")
MAINTENANCE_PLACES = ("Upper knife", "Lower knife", "Belt", "Shear knife",
                     "Slitter/scorer", "Double backer", "Splicer 1", "Splicer 2",
                     "Splicer 3", "Splicer 4", "Splicer 5", "Glue dams")
# Compatible cutoff pairs avoid enormous least-common-multiple segments.
LENGTH_PAIRS = ((20,40), (24,36), (30,45), (36,54), (40,60),
                (46,92), (60,90), (32,64), (48,72), (56,84))


def _groups(rng):
    """Generate compatible orders before allocating any production time."""
    group = 0
    while True:
        width = rng.choice((98,95,92,87))
        grade = list(GRADES)[group % len(GRADES)]
        single = group % 5 == 4
        if single:
            w = rng.choice([w for w in range(12,61) if 2 <= width-((width-2)//w)*w <= 8])
            outs = (width-2)//w
            specs = [(w, outs, rng.randint(20,92))]
        else:
            # Sample order dimensions and outs, retaining feasible centered fits.
            while True:
                w1, w2 = rng.randint(12,60), rng.randint(12,60)
                o1, o2 = rng.randint(1,3), rng.randint(1,3)
                occupied = w1*o1+w2*o2
                if 2 <= width-occupied <= 8:
                    break
            lengths = rng.choice(LENGTH_PAIRS)
            specs = [(w1,o1,lengths[0]), (w2,o2,lengths[1])]
        common = lcm(*(s[2] for s in specs))
        count = 1 if single else rng.choice((1,2,2,3,4))
        travels, demands = [], []
        for _ in range(count):
            # Demand is individual sheets, not cuts. Round to a common cutoff
            # boundary; surplus is explicit in the order ledger.
            demand = rng.randint(650,1750)
            driver = specs[-1]
            inches = ceil(demand/driver[1]*driver[2]/common)*common
            travels.append(inches)
            demands.append(demand)
        orders = []
        for i, spec in enumerate(specs):
            if i == 0 and not single:
                planned = sum(travels)//spec[2]*spec[1]
                orders.append({"id":f"O{group}-U", "requested":planned,
                               "planned":planned, "produced":0})
            else:
                orders.append(None)
        for index, inches in enumerate(travels):
            segment_orders = list(orders)
            driver = specs[-1]
            segment_orders[-1] = {"id":f"O{group}-{index}", "requested":demands[index],
                                  "planned":inches//driver[2]*driver[1], "produced":0}
            yield {"group":group, "grade":grade, "width":width, "specs":specs,
                   "orders":segment_orders, "remaining_inches":inches,
                   "common":common, "setup":f"C{group}-{index}", "new":True}
        group += 1


def generate(seed=7, production_day="2026-09-01", corrupt=False):
    rng, quality = random.Random(seed), random.Random(seed+10000)
    plans = _groups(rng)
    plan = next(plans)
    root = ET.Element("production", version="1", synthetic="true",
                      generator="v4-quantity-orders", production_day=production_day,
                      clock="fixed-demo-local")
    origin = datetime.fromisoformat(production_day+"T07:00:00")
    for shift in (1,2,3):
        begin = origin+timedelta(hours=8*(shift-1))
        finish = begin+timedelta(hours=8)
        node = ET.SubElement(root, "shift", number=str(shift), start=begin.isoformat(),
            end=finish.isoformat(), notes=(
                "Cool, damp weather reported; inspect recorded bond observations.",
                "Crew reported a flute transition difficulty; recorded symptoms need review.",
                "Crew reported increased rejects; check quality records with the crew.")[shift-1])
        cursor, n = begin, 0
        while cursor < finish:
            target = GRADES[plan["grade"]]
            running_fpm = target*rng.uniform(*((.66,.73) if shift==2 else (.84,.89)))
            remaining_seconds = (finish-cursor).total_seconds()
            stop_seconds = (240 if shift==2 else 60) if n in (8,18,28,38,48) else 0
            if n in (8,18,28):
                stop_seconds += (min(3,max(0,(seed-7)//7))*30 if stop_seconds else 0)
            stop_seconds = min(stop_seconds, max(0,remaining_seconds-30))
            shear_feet = 17 if plan["new"] else 0
            full_seconds = (plan["remaining_inches"]/12+shear_feet)/running_fpm*60+stop_seconds
            boundary = full_seconds >= remaining_seconds
            if boundary:
                capacity = max(0,(remaining_seconds-stop_seconds)/60*running_fpm-shear_feet)*12
                inches = min(plan["remaining_inches"], floor(capacity/plan["common"])*plan["common"])
                # If less than one cutoff quantum remains, consume the final
                # seconds as explicit idle on the prior frame, never invent cuts.
                if inches == 0:
                    prior = node.findall("setup")[-1] if node.findall("setup") else None
                    if prior is None:
                        raise ValueError("Synthetic shift has no production capacity")
                    prior.set("end", finish.isoformat())
                    prior.set("boundary_idle_seconds", str(float(prior.get("boundary_idle_seconds","0"))+remaining_seconds))
                    break
                end = finish
            else:
                inches = plan["remaining_inches"]
                end = cursor+timedelta(seconds=full_seconds)
                # Avoid a microscopic extra frame caused by timestamp rounding.
                if (finish-end).total_seconds() < .001:
                    end = finish
            net_feet = inches/12
            gross_feet = net_feet+shear_feet
            occupied = sum(w*o for w,o,_ in plan["specs"])
            rejects = min(quality.randint(5,20) if shift!=3 else quality.randint(30,85),
                          inches//plan["specs"][0][2]*plan["specs"][0][1])
            reject_area = rejects*plan["specs"][0][0]*plan["specs"][0][2]/144
            idle = max(0,(end-cursor).total_seconds()-stop_seconds-gross_feet/running_fpm*60)
            setup = ET.SubElement(node,"setup",id=f"S{shift}-{n:02}", setup_id=plan["setup"],
                wet_end_id=f"W{plan['group']:03}", grade=plan["grade"], target_fpm=str(target),
                start=cursor.isoformat(), end=end.isoformat(), width_in=str(plan["width"]),
                gross_feet=str(gross_feet), gross_sqft=str(gross_feet*plan["width"]/12),
                trim_sqft=str(99999999 if corrupt and shift==2 and n==0 else net_feet*(plan["width"]-occupied)/12),
                shear_sqft=str(shear_feet*plan["width"]/12),
                reject_sheets=str(rejects), reject_sqft=str(reject_area),
                reject_reason=quality.choice(REJECT_REASONS), running_fpm=str(running_fpm),
                running_seconds=str(gross_feet/running_fpm*60), boundary_idle_seconds=str(idle))
            left = (plan["width"]-occupied)/2
            for i, ((w,outs,length),order) in enumerate(zip(plan["specs"],plan["orders"])):
                produced = inches//length*outs
                before = order["planned"]-order["produced"]
                order["produced"] += produced
                level = ("upper","lower")[i]
                ET.SubElement(setup,"knife",level=level,stacker=level,order_id=order["id"],
                    width_in=str(w),length_in=str(length),outs=str(outs),start_in=str(left),
                    cuts=str(inches//length),order_quantity=str(order["requested"]),
                    planned_sheets=str(order["planned"]),produced_sheets=str(produced),
                    remaining_before=str(before),remaining_after=str(before-produced))
                left += w*outs
            if stop_seconds:
                place = "Upper knife" if n in (8,18,28) else MAINTENANCE_PLACES[(seed+shift+n)%len(MAINTENANCE_PLACES)]
                reason = "Knife jam" if n in (8,18,28) else "Equipment stop"
                if n==38: place,reason = f"Splicer {(seed+shift)%5+1}","Missed splice"
                ET.SubElement(node,"stop",id=f"D{shift}-{n}",start=cursor.isoformat(),
                    end=(cursor+timedelta(seconds=stop_seconds)).isoformat(),
                    kind="Operator" if n==38 else "Maintenance",place=place,reason=reason,
                    notes="Synthetic reported symptom; root cause and corrective action require human review.")
            plan["remaining_inches"] -= inches
            plan["new"] = False
            if not plan["remaining_inches"]: plan = next(plans)
            cursor, n = end, n+1
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
