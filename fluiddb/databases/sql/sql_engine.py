import sqlite3
from fluiddb.database.database_engine import DatabaseEngine


class SQLEngine(DatabaseEngine):

    def __init__(self, db_id: str):
        self.db = None
        self.db_id = db_id

    @property
    def engine_name(self) -> str:
        return "SQL"
        
    def db_connection(self, db_id: str):
        self.db = sqlite3.connect(f'{self.db_id}.db')
        return self.db

    def query(self, query: str):
        db = self.db_connection(self.db_id)
        cur = db.cursor()
        result = cur.execute(query).fetchall()
        db.commit()
        db.close()
        return result

    def schema(self) -> str:
        db = self.db_connection(self.db_id)
        schema_str = ""

        db.text_factory = str
        cur = db.cursor()

        table_names = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = list(map(lambda l: l[0], table_names))

        for table_name in table_names:
            if "sqlite_" in table_name:
                continue
            schema_str += f"{table_name}\n"
            result = cur.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            for r in result:
                schema_str += f"  {r[1]}: {r[2]} {'pk' if r[5] else ''}\n"

        cur.close()
        db.close()

        return schema_str
    
    def dump(self):
        db = self.db_connection(self.db_id)

        db.text_factory = str
        cur = db.cursor()

        table_names = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = list(map(lambda l: l[0], table_names))

        for table_name in table_names:
            if "sqlite_" in table_name:
                continue
            print(table_name)
            result = cur.execute(f"SELECT * from '{table_name}'").fetchall()
            print(result)
            print()

        cur.close()
        db.close()


if __name__ == '__main__':
    memory = SQLEngine()

    # db = memory.db_connection("test")
    # cursor = db.cursor()
    # cursor.execute('''
    #     CREATE TABLE if not exists test_table(
    #         id INTEGER PRIMARY KEY,
    #         name TEXT,
    #         age INTEGER
    #     )
    # ''')
    # cursor.execute("INSERT INTO test_table (name, age) VALUES (?, ?)", ('Alice', 25))
    # cursor.execute("INSERT INTO test_table (name, age) VALUES (?, ?)", ('Bob', 30))
    # db.commit()
    # cursor.close()


    schema = memory.schema("test")
    print(schema)
