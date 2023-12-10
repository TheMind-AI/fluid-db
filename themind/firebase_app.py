import os
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv

load_dotenv()

if os.getenv('ENV') == 'dev':
    firebase_key = '/Users/zvada/Documents/TheMind/TheMind/firebase-pk.json'

else:
    firebase_key = os.getcwd() + '/firebase-pk.json'

cred = credentials.Certificate(firebase_key)
firebase_app = firebase_admin.initialize_app(cred)