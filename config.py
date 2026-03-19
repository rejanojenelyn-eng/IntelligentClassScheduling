import os

class Config:
    # Database connection details
    DB_NAME = "DBwithRoomFaculty"
    DB_USER = "postgres"
    DB_PASS = "050105"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    
    # Secret key for Flask sessions
    SECRET_KEY = os.urandom(24)