from py4web import DAL
from py4web.utils.auth import Auth

db = DAL('sqlite://recipes_final.db', folder=None)
auth = Auth(None, db)  # Pass None for session, py4web will handle it