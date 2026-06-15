import os

class Config:
    # Database connection details
    DB_NAME = "ASDBv10"
    DB_USER = "postgres"
    DB_PASS = "051705"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    
    # Secret key for Flask sessions
    SECRET_KEY = os.urandom(24)