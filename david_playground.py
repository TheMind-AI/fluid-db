import json

import jsonpath_ng.ext

from themind.functions.update_memory_function import UpdateMemoryFunction
from themind.memory.structured_json_memory import JsonPathExpr, StructuredJsonMemory


def main():




    # func = UpdateMemoryFunction()
    # uid = "test2"
    #
    # func.run(uid, "Adams phone number is 722238738")

    # print(repr(JsonPathExpr(json_path='$.phone')))

    # print(StructuredJsonMemory.simplify_jsonpath("phones[?name = \"Adam\"].number"))

    # memory = StructuredJsonMemory()
    # uid = "a"
    #
    # schema = memory.schema(uid)
    # print("SCHEMA:")
    # print(json.dumps(schema, indent=2))
    #
    # memory.update(uid, "$.phones", [{"name": "David", "number":"123456"}], "Name and phone number of people I know")
    #
    # schema = memory.schema(uid)
    # print("SCHEMA:")
    # print(json.dumps(schema, indent=2))
    #
    # desc = memory.get_descriptions(uid)
    # print("DESC:")
    # print(json.dumps(desc, indent=2))

    # print("$.phones[*].country", "comment: Country of the phone number")

    # memory.update("2", "$.phones", {"name": "Jirka", "number": "122"})

    # print(json.dumps(schema, indent=2))
    #
    # memory_data = memory.get_memory("2")
    # print("MEMORY DATA:")
    # print(json.dumps(memory_data, indent=2))
    #
    # memory.append("2", "$.phones", {"name": "Jakub", "number": "123"})
    #
    # memory_data = memory.get_memory("2")
    # print("MEMORY DATA:")
    # print(json.dumps(memory_data, indent=2))
    #
    # return

    def mod_func(orig, data, field):
        data[field] = {
            "name": orig["name"],
            "numbers": [
                orig["number"]
            ]
        }

    data = {
      "phones": [
        {
          "name": "Adam",
          "number": "123456"
        },
        {
          "name": "David",
          "number": "654321"
        }
      ],
      "user": {
        "name": "David"
      },
      "events": [
        "Founders Inc Christmas Party",
        "Christmas Eve"
      ]
    }

    expr = jsonpath_ng.ext.parse("$.phones[?(@.name =~ 'Adam')]|$.user")

    print([v.value for v in expr.find(data)])

    # try:
    #     expr = jsonpath_ng.ext.parse("$.phone)")
    # except Exception as e:
    #     print(e)

    def update(json_path: str, data, val):
        expr = jsonpath_ng.ext.parse(json_path)
        # expr.find_or_create(data)
        expr.update_or_create(data, val)


    # update("events", data, "Mokos")
    # update("phones[*]", data, mod_func)
    # update('phones[?name = "Adam"].number', data, "722264238")

    # update("events_new", data, [{"name": "AI Summit", "date": "2023-12-12"}])
    #
    # print(json.dumps(data, indent=4))


    # expr = parse("user.last_name")
    # print([f.value for f in expr.find(data)])
    #
    # expr.update(data, "David")


    # expr = parse("events[*]")
    # print([f.value for f in expr.find(data)])
    #
    # expr.update(data, "David")

    # expr = parse("phones[*]")
    # expr.update(data, mod_func)

    # print(json.dumps(data, indent=4))

    # uid = '1'
    #
    # memory = StructuredJsonMemory()
    #
    # res = memory.query(uid, "name")
    # memory.append_to_list(uid, "events", {
    #         "location": "",
    #         "time": "18:00",
    #         "theme": "AI"
    #     })
    # memory.append_to_list(uid, "test_list", {"location": "Golden Gate", "time": "12:00"})
    #
    # res = memory.query(uid, "events")
    # print(res)
    #
    # print(memory.get_memory(uid))
    # print(memory.schema(uid))


if __name__ == "__main__":
    main()
