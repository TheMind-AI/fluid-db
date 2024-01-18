import json

def summarize_object(obj):
    if isinstance(obj, dict):
        # Create a summary for the dictionary
        summary = ', '.join([f"{k}: {str(v)}" for k, v in obj.items() if not isinstance(v, (dict, list))])
        return summary[:50]  # Truncate to first 50 characters
    return str(obj)[:50]  # Handle non-dict objects


def calculate_embeddings(obj):
    return hash(str(obj))


def process_json(obj, parent_path='', results=None):
    if results is None:
        results = []

    if isinstance(obj, dict):
        full_path = f"{parent_path}" if parent_path else "root"

        shallow_obj = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        if shallow_obj:
            new_obj = {full_path: shallow_obj}
            results.append((new_obj, calculate_embeddings(new_obj)))

        for key, value in obj.items():
            child_path = f"{parent_path}.{key}" if parent_path else key
            if isinstance(value, (dict, list)):
                process_json(value, child_path, results)

    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            list_summary = summarize_object(item)
            list_path = f"{parent_path}[{idx}] ({list_summary})"
            process_json(item, list_path, results)

    return results


if __name__ == '__main__':

    # Process the JSON data
    json_path = "/data/alex-rivera.json"
    with open(json_path, 'r') as file:
        json_obj = json.load(file)

    processed_data = process_json(json_obj)

    # Output the processed data
    for obj, embedding in processed_data:
        print(json.dumps(obj, indent=4), "Embedding:", embedding)

