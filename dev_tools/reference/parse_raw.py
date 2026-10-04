# read textData.csv
import csv
with open('textData.csv', 'r') as f:
    reader = csv.reader(f)
    data = list(reader)[1:]
# parse data
# id,voiceId,faceId,skitPosition,mapName,eventName,character,jp,en,kr,kn,hn
# keep: id, eventName, character,[jp], [kn]
character = []
conversation = []
flushed = True
for row in data:
    if row[0].startswith("name_"):
        character.append({
            "id": row[0],
            "jp": row[7],
            "kn": row[10]
        })
    elif flushed and row[0].startswith("txt_"):
        conversation.append({
            "id": row[0],
            "eventName": row[5],
            "character": row[6],
            "jp": [row[7]],
            "kn": [row[10]]
        })
        flushed = False
    elif not flushed and not row[7]:
        flushed = True
    elif not flushed and row[7]:
        conversation[-1]["jp"].append(row[7])
        conversation[-1]["kn"].append(row[10])

# write to json
import json
data = {
    "character": character,
    "conversation": conversation
}
with open('parsedText.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)