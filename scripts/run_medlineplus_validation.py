import csv
import xml.etree.ElementTree as ET
from pathlib import Path

XML_FILE = Path("data/medlineplus/mplus_topics.xml")
OUTPUT_FILE = Path("eval/results/medlineplus_topics.csv")

KEYWORDS = [
    "diabetes",
    "a1c",
    "kidney",
    "chronic kidney disease",
    "high blood pressure",
    "drug reactions",
    "allergy",
    "diabetes medicines",
]

print("=" * 70)
print("CARETRACE / MEDLINEPLUS TOPIC BENCHMARK")
print("=" * 70)

root = ET.parse(XML_FILE).getroot()
topics = root.findall("health-topic")

print(f"Total MedlinePlus records: {len(topics)}")

selected = []

for topic in topics:
    title = topic.attrib.get("title", "")
    title_lower = title.lower()

    matched = [
        keyword
        for keyword in KEYWORDS
        if keyword.lower() in title_lower
    ]

    if matched:
        selected.append({
            "id": topic.attrib.get("id", ""),
            "title": title,
            "language": topic.attrib.get("language", ""),
            "url": topic.attrib.get("url", ""),
            "matched_keywords": ", ".join(matched),
            "has_summary": topic.find("full-summary") is not None,
        })

print(f"Relevant records found: {len(selected)}")
print()

for item in selected:
    print(
        f"{item['id']} | "
        f"{item['title']} | "
        f"{item['language']} | "
        f"summary={item['has_summary']}"
    )

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "id",
            "title",
            "language",
            "url",
            "matched_keywords",
            "has_summary",
        ],
    )
    writer.writeheader()
    writer.writerows(selected)

print()
print("=" * 70)
print(f"Saved: {OUTPUT_FILE}")
print("=" * 70)