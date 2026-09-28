import pprint

import app
from database import query_db

sid = query_db(
    """
    SELECT s.sectionid
    FROM sections s
    JOIN program_yearlevel p
      ON p.programyearlevelid = s.programyearlevelid
    WHERE p.programcode = 'BEED'
      AND p.yearlevel = 3
      AND p.academicyearid = 'AY2627'
    LIMIT 1
    """,
    one=True
)["sectionid"]

body = app.app.test_client().post(
    "/api/schedule/retrieve-previous",
    json={
        "program": "BEED",
        "yearLevel": 3,
        "term": "A",
        "acadYear": "AY2627",
        "section": str(sid),
    },
).get_json()

print("\nSECTION:")
print(sid)

print("\n========== ELED 317 ==========")
pprint.pp([
    row
    for row in body["schedule_data"]
    if row.get("subject_code") == "ELED 317"
])

print("\n========== HC10 CONFLICTS ==========")
pprint.pp([
    conflict
    for conflict in body["evaluation"]["conflicts"]
    if conflict["rule"] == "HC10"
])