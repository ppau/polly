import pymongo
from pymongo import Connection
import string


def create_example_peons(collection):
	collection.drop()

	collection.insert({"id": 'A', "delegate": 'D', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'B', "delegate": 'D', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'C', "delegate": 'D', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'D', "delegate": 'G', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'E', "delegate": 'G', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'F', "delegate": 'G', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'G', "delegate": 'H', 'effectiveDelegate': 'H'})
	collection.insert({"id": 'H', "delegatedBy": ['A', 'B', 'C', 'D', 'E', 'F', 'G']})


def find_effective_delegate(collection, record):
	return collection.find_one({"id": record['effectiveDelegate']})


def set_delegate(collection, id, delegate):
	delegater = collection.find_one({"id": id})
	delegated = collection.find_one({"id": delegate})
	
	# Check that id doesn't have own delegates
	if delegater.get('delegatedBy', None):
		collection.update(
			{"effectiveDelegate": id}, 
			{"$set": {"effectiveDelegate": delegate}}, 
			upsert=False,
			multi=True
		)

		# Remove self from list
		delegater['delegatedBy'].remove(delegate)
		
		# Merge delegated list
		collection.update(
			{"id": delegate},
			{"$pushAll": {"delegatedBy": delegater['delegatedBy'] + [id]}},
			upsert=False
		)
		
	collection.update(
		{"id": id},
		{
			"$unset": {"delegatedBy": 1},
			"$set": {
				"delegate": delegate,
				"effectiveDelegate": delegate
			}
		},
		upsert=False
	)


def main():
	connection = Connection('localhost')
	collection = connection.pollyPrototypes.delegateChains
	
	create_example_peons(collection)
	set_delegate(collection, 'H', 'A')
	set_delegate(collection, 'A', 'H')


if __name__ == "__main__":
	main()
