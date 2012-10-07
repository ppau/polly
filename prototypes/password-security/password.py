import scrypt
import uuid

# XXX: should be cryptographically secure random bytes
SYSTEM_SALT = uuid.uuid4().hex

def generate_user_salt():
	return uuid.uuid4().hex # XXX: need crypto random here too

def hash_password(password, maxtime=2.0):
	user_salt = generate_user_salt()
	return {
		"salt": user_salt,
		"hash": scrypt.encrypt(SYSTEM_SALT + user_salt, password, maxtime)
	}

def verify_password(password, hash, salt, maxtime=4.0):
	try:	
		computed_hash = scrypt.decrypt(hash, password, maxtime)
		return SYSTEM_SALT + salt == computed_hash
	except scrypt.error:
		return False

