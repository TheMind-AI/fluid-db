import sqlite3


class StructuredSQLMemory:

    def __init__(self):
        self.db = None

    def query(self, uid: str, query: str):
        db = self.db_connection(uid)
        cur = db.cursor()
        result = cur.execute(query).fetchall()
        db.commit()
        db.close()
        return result

    def schema(self, uid: str) -> str:
        db = self.db_connection(uid)
        schema_str = ""

        db.text_factory = str
        cur = db.cursor()

        table_names = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = list(map(lambda l: l[0], table_names))

        for table_name in table_names:
            schema_str += f"{table_name}\n"
            result = cur.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            for r in result:
                schema_str += f"  {r[1]}: {r[2]} {'pk' if r[5] else ''}\n"

        cur.close()
        db.close()

        return schema_str


    def db_connection(self, uid):
        self.db = sqlite3.connect(f'{uid}.db')
        return self.db

    def dump(self, uid):
        db = self.db_connection(uid)

        db.text_factory = str
        cur = db.cursor()

        table_names = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = list(map(lambda l: l[0], table_names))

        for table_name in table_names:
            print(table_name)
            print()

            result = cur.execute(f"SELECT * from '{table_name}'").fetchall()
            print(result)

        cur.close()
        db.close()


if __name__ == '__main__':
    memory = StructuredSQLMemory()

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
