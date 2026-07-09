import os

class Config:
    
    DB_NAME = "ASDBv10"
    DB_USER = "postgres"
    DB_PASS = "050105"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    
    SECRET_KEY = os.urandom(24)