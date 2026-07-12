import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = 'super-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///app.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
