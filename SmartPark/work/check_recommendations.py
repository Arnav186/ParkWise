import sys
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import recommend

db = sqlite3.connect(':memory:')
db.row_factory = sqlite3.Row
db.execute('CREATE TABLE parking_areas (id INTEGER, name TEXT, location TEXT, capacity INTEGER, occupied INTEGER, updated_at TEXT)')
db.executemany('INSERT INTO parking_areas VALUES (?, ?, ?, ?, ?, ?)', [
    (1, 'North Gate', '', 60, 32, '2026-10-13T00:00:00Z'),
    (2, 'Library Lot', '', 40, 37, '2026-10-13T00:00:00Z'),
    (3, 'Sports Complex', '', 80, 80, '2026-10-13T00:00:00Z'),
    (4, 'Unmapped', '', 20, 0, '2026-10-13T00:00:00Z')])
destination = {'walkMinutes': {'North Gate':4, 'Library Lot':1, 'Sports Complex':8}}
result = recommend(db, destination, 'balanced')
assert result['options'][0]['id'] == 1
assert result['fullAreas'] == 1 and result['unmappedAreas'] == 1
assert 'closer' in result['reason']
assert recommend(db, destination, 'walk')['options'][0]['id'] == 2
assert recommend(db, destination, 'space')['options'][0]['id'] == 1
db.execute('UPDATE parking_areas SET occupied=0 WHERE id=2')
assert recommend(db, destination, 'balanced')['options'][0]['id'] == 2
db.execute('UPDATE parking_areas SET occupied=capacity')
assert recommend(db, destination, 'balanced')['options'] == []
db.execute('DELETE FROM parking_areas')
assert recommend(db, destination, 'balanced')['options'] == []
db.close()
print('PASS: preferences, occupancy changes, full areas, unmapped areas, empty facility.')
