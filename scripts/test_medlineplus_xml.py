import xml.etree.ElementTree as ET
from pathlib import Path

XML_FILE = Path("data/medlineplus/mplus_topics.xml")

print("=" * 70)
print("MEDLINEPLUS XML STRUCTURAL VALIDATION")
print("=" * 70)

root = ET.parse(XML_FILE).getroot()
topics = root.findall("health-topic")

total = len(topics)
valid = 0
invalid = []

for topic in topics:
    title = topic.attrib.get("title")
    topic_id = topic.attrib.get("id")
    language = topic.attrib.get("language")
    url = topic.attrib.get("url")
    summary = topic.find("full-summary")

    if title and topic_id and language and url and summary is not None:
        valid += 1
    else:
        invalid.append({
            "id": topic_id,
            "title": title,
            "language": language,
            "url": bool(url),
            "summary": summary is not None,
        })

print(f"Total records : {total}")
print(f"Valid records : {valid}")
print(f"Invalid       : {len(invalid)}")

if total:
    print(f"Validity      : {(valid / total) * 100:.2f}%")

print()

if invalid:
    print("INVALID RECORDS:")
    for item in invalid:
        print(item)
else:
    print("PASS: All records passed structural validation.")