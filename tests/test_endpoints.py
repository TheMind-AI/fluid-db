import requests
import json

# Base URL of your application. Change this to your actual application URL.
BASE_URL = "http://localhost:8081"


def test_user_endpoint():
    # Test creating a user
    response = requests.post(f"{BASE_URL}/user", json={"name": "Test User", "email": "test@example.com"})
    assert response.status_code == 200
    uid = response.json()["uid"]

    # Test getting the user we just created
    response = requests.get(f"{BASE_URL}/user/{uid}")
    assert response.status_code == 200
    assert response.json()["name"] == "Test User"
    assert response.json()["email"] == "test@example.com"
    
    return uid


def test_chat_endpoint(uid):
    response = requests.post(f"{BASE_URL}/chat", json={"uid": uid, "content": "test_content"}, stream=True)
    
    # Check that the thread_id is in the headers
    assert 'Thread-ID' in response.headers

    # The response is a stream, so we can't use response.json() directly.
    # Instead, we'll iterate over the lines in the response.
    for line in response.iter_lines():
        # Each line is a chunk of the response. We need to decode it from bytes to a string,
        # and then parse it as JSON.
        chunk = json.loads(line.decode())
        
        # Now we can assert on the contents of the chunk.
        assert "response_stream" in chunk


def test_memory_endpoint(uid):
    # Test getting the memory for a user
    response = requests.get(f"{BASE_URL}/memory/{uid}")
    assert response.status_code == 200
    assert "user_memory" in response.json()

# Run the tests
uid = test_user_endpoint()
test_chat_endpoint(uid)
test_memory_endpoint(uid)