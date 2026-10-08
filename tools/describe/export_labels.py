"""Лейблы для describe из базы discocs — запускать внутри контейнера бэкенда (база открывается только на чтение):

    docker cp export_labels.py discocs-backend-1:/tmp/ && docker exec discocs-backend-1 python /tmp/export_labels.py [N] [MIN_RELEASES] > labels.json

Берёт лейблы без описания, у которых в библиотеке хотя бы MIN_RELEASES релизов (по умолчанию 3): самые
крупные первыми, N штук (по умолчанию 20).
Временный путь до интеграции describe с API discocs.
"""
import json
import sqlite3
import sys

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
min_releases = int(sys.argv[2]) if len(sys.argv) > 2 else 3
db = sqlite3.connect("file:/app/data/app.db?mode=ro", uri=True)
rows = db.execute("""
    select l.id from labels l join release_labels rl on rl.label_id = l.id
    where l.description is null
    group by l.id having count(distinct rl.release_id) >= ?
    order by count(distinct rl.release_id) desc, l.id limit ?""", (min_releases, limit)).fetchall()
labels = []
for (label_id,) in rows:
    name, external = db.execute("select name, external_ids_json from labels where id = ?", (label_id,)).fetchone()
    # Артисты и релизы из библиотеки — по ним агент узнаёт «тот ли это лейбл» на найденных страницах.
    artists = [r[0] for r in db.execute("""
        select a.name from release_labels rl join release_artists ra on ra.release_id = rl.release_id
        join artists a on a.id = ra.artist_id where rl.label_id = ? and a.normalized_name != 'various artists'
        group by a.id order by count(*) desc limit 12""", (label_id,))]
    releases = [r[0] for r in db.execute("""
        select r.title || coalesce(' (' || r.release_year || ')', '') from release_labels rl
        join releases r on r.id = rl.release_id where rl.label_id = ? order by r.release_year desc limit 30""",
        (label_id,))]
    labels.append({"id": label_id, "name": name, "external_ids": json.loads(external or "{}"),
                   "artists": artists, "releases": releases})
print(json.dumps(labels, ensure_ascii=False, indent=1))
