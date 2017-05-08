#!flask/bin/python
import os
from flask import Flask, jsonify, request, redirect, url_for, render_template
from flaskrun import flaskrun
from boto.dynamodb2 import connect_to_region
from boto.dynamodb2.fields import HashKey
from boto.dynamodb2.table import Table
from hashids import Hashids
from urllib.parse import urlparse
from boto.exception import JSONResponseError
from boto.dynamodb2.exceptions import ConditionalCheckFailedException, ItemNotFound
from boto.dynamodb2.types import NUMBER
import sys

### For local testing
from boto.dynamodb2.layer1 import DynamoDBConnection

# Connect to DynamoDB Local
# local_connection = DynamoDBConnection(
#     host='localhost',
#     port=8000,
#     aws_access_key_id='anything',
#     aws_secret_access_key='anything',
#     is_secure=False)

application = Flask(__name__)

# conn = local_connection

conn = connect_to_region('us-west-2',
                                       aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
                                       aws_secret_access_key=os.environ['AWS_SECRET_KEY'])

### Dynamodb table initialisation

def bootstrap_db():
    metadata_table = Table.create('metadata', schema=[HashKey('metadata_key')], connection=conn)
    shorturls_table = Table.create('shorturls', schema=[HashKey('shorturl_key', data_type=NUMBER)], connection=conn)

    metadata_table.put_item(data={
        'metadata_key': 'last_stored_id',
        'value': 0
    })


metadata_table = Table('metadata', connection=conn)
shorturls_table = Table('shorturls', connection=conn)

try:
    metadata_table.describe()

except JSONResponseError:
    bootstrap_db()

app = Flask(__name__)

app.config['SHORTURL_MAX_RETRIES'] = 5

# Hashids salt for obfuscating the hash sequence
# https://github.com/davidaurelio/hashids-python
app.config['HASHID_SALT'] = "saltysalty"

hashid_encoder = Hashids(app.config['HASHID_SALT'])

""" URL Shortener

This application is a proof-of-concept to demonstrate the business logic for the URL Shortener project

It provides two endpoints, /create and /w/<short_url>

/create accepts POST requests, with one parameter being the destination URL
Upon receiving a POST request the application runs the function 
to generate a shortened URL and returns that to the caller

/w/<short_url> redirects the caller to the destination URL that is retrieved using <short_url> as the key


Short URL Generation:

URL mappings are keyed by an incrementing counter, stored as decimal integers in the key-value store.
They are converted to base 62 ([a-z][A-Z][0-9]) for use as the short URL path. 
Hashids was selected instead of rolling my own base conversion function as 
it generates strings with desirable properties. [1] 
[1]: https://github.com/davidaurelio/hashids-python

"""


@application.route('/')
def main_page():
    return render_template('index.html')


@application.route('/w/<short_url>', methods=['GET'])
def redirect_shorturl_endpoint(short_url):
    try:
        shorturl_key = hash_to_base10_int(short_url)
        destination_url = retrieve_short_url_destination(shorturl_key)
    except (ValueError, ItemNotFound):
        return jsonify(message="Invalid Short URL"), 400

    return redirect(destination_url, 301)


@application.route('/create', methods=['POST'])
def create_shorturl_endpoint():
    """ Algorithm
    1. Check user credentials (not implemented)
    2. Validate destination_url
    
    Begin (pseudo) Transaction - boto3 doesn't do real transactions unlike Java's AWS library
    3. Retrieve last_stored_id from metadata table
    4. Attempt to write last_stored_id(+1): destination_url to short_urls table
    5. If successful, increment last_stored_id and update value in metadata table
        5.1 If unsuccessful, retry from 3.
    End (pseudo) Transaction
    
    6. Return short_url to the caller
    
    It's alright to play a little loose with last_stored_id, as the only thing we are concerned about is
    not ending up overriding an existing short_url. This is enforced already by the database putItem call,
    so the last_stored_id is just to make sure we don't have to try all the keys starting from 0.
    If it is imperative for every short_url to be consecutive integers then consider writing a semaphore 
    to put a lock on the last_stored_id in the database.
    
    :return: HTTP response containing the short_url, or exception
    """

    try:
        if validate_dest_url(request.form['destination_url']):
            destination_url = request.form['destination_url']

    except ValueError:
        return jsonify(message="Malformed Destination URL"), 400

    last_stored_id = retrieve_last_stored_id()

    for _ in range(app.config['SHORTURL_MAX_RETRIES']):
        next_id = last_stored_id + 1
        try:
            result = put_short_url(next_id, destination_url)
            print(result, file=sys.stderr)

        except ConditionalCheckFailedException:  # This exception shows up when key exists in database
            continue  # Try again with a higher key

        else:  # This executes if we don't run into an exception
            increment_last_stored_id(next_id)
            break

    else:  # If we run out of retries, HTTP 503
        return jsonify(message="Service unavailable right now, please try again later"), 503

    short_url = generate_short_url(next_id)
    return jsonify(short_url=short_url)


def put_short_url(last_stored_id, destination_url):
    """ Saves shorturl mapping to database
    :raises: ConditionalCheckFailedException if key already exists
    :return: True if commit successful
    """
    return shorturls_table.put_item(data={
        'shorturl_key': last_stored_id,
        'destination_url': destination_url
    }, overwrite=False)


def retrieve_last_stored_id():
    """ Retrieve last_stored_id from metadata table """
    return int(metadata_table.get_item(metadata_key='last_stored_id')['value'])


def increment_last_stored_id(last_stored_id):
    """ Saves last_stored_id to metadata table """
    return metadata_table.put_item(data={
        'metadata_key': 'last_stored_id',
        'value': last_stored_id
    }, overwrite=True)


def validate_dest_url(dest_url):
    """ Simplistic URL validation 
    Runs urllib.urlparse on the given URL
    
    :raises: ValueError if validation fails
    :return: True otherwise
    """
    result = urlparse(dest_url)
    if result.scheme == '' or result.netloc == '':
        raise ValueError

    else:
        return True


def hash_to_base10_int(hash_string):
    """ Converts hashid string to base 10 integer
    :raises: ValueError if string doesn't decode to a single base 10 integer
    """
    result = hashid_encoder.decode(hash_string)
    if len(result) != 1:
        raise ValueError

    return result[0]


def generate_short_url(base10_int):
    """ Generates full short_url
     Runs hashids encoding function and appends it to the host's URL
     
    :return: Fully qualified URL
    """
    host_url = urlparse(request.url)
    return host_url.scheme + "://" + host_url.netloc + "/w/" + hashid_encoder.encode(base10_int)


def retrieve_short_url_destination(shorturl_key):
    """ Retrieves destination URL from database 
    :raises: ItemNotFound if key doesn't exist
    :return: Destination URL String
    """
    return shorturls_table.get_item(shorturl_key=shorturl_key)['destination_url']


if __name__ == '__main__':
    flaskrun(application)
