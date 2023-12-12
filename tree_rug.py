import json

# Example JSON data
json_data = {
    "personal-info": {
        "name": "Alex Smith",
        "contact": {
            "email": "alex.smith@example.com",
            "phone": "123-456-7890"
        },
        "education": {
            "university": {
                "name": "State University",
                "date": "2010-2014",
                "degree": "B.Sc. Computer Science"
            },
            "highSchool": {
                "name": "Central High School",
                "date": "2006-2010"
            }
        },
        "workExperience": [
            {
                "company": "TechCorp",
                "position": "Software Engineer",
                "years": "2015-2018",
                "details": {
                    "projects": ["Project Alpha", "Project Beta"],
                    "manager": "John Doe"
                }
            },
            {
                "company": "Innovatech",
                "position": "Senior Developer",
                "years": "2018-2021",
                "details": {
                    "projects": ["Project Gamma", "Project Delta"],
                    "manager": "Jane Smith"
                }
            }
        ]
    },
    "hobbies": ["Reading", "Hiking", "Photography"],
    "skills": {
        "programmingLanguages": ["Python", "JavaScript", "C#"],
        "frameworks": ["React", "Django", "ASP.NET"]
    },
    "preferences": {
        "workEnvironment": {
            "remote": True,
            "teamSize": "5-10"
        },
        "desiredSalary": {
            "currency": "USD",
            "amount": "100000"
        }
    }
}

def calculate_embeddings(obj):
    # Dummy function - Replace with your actual embedding calculation
    return hash(str(obj))

def process_json(obj, parent_path='', results=None):
    if results is None:
        results = []

    full_path = f"{parent_path}" if parent_path else "root"

    if isinstance(obj, dict):
        # Process this node by considering only non-dict and non-list values
        shallow_obj = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        if shallow_obj:  # Check if there is something to add
            new_obj = {full_path: shallow_obj}
            results.append((new_obj, calculate_embeddings(new_obj)))

        for key, value in obj.items():
            child_path = f"{full_path}.{key}" if parent_path else key
            if isinstance(value, (dict, list)):
                # Recursively process child nodes
                process_json(value, child_path, results)

    elif isinstance(obj, list):
        # Process the list as a whole if it contains non-dict elements
        if not all(isinstance(item, (dict, list)) for item in obj):
            new_obj = {parent_path: obj}
            results.append((new_obj, calculate_embeddings(new_obj)))
        else:
            # Process list elements
            for idx, item in enumerate(obj):
                list_path = f"{parent_path}[{idx}]"
                process_json(item, list_path, results)

    return results

# Process the JSON data
processed_data = process_json(json_data)

# Output the processed data
for obj, embedding in processed_data:
    print(json.dumps(obj, indent=4), "Embedding:", embedding)
