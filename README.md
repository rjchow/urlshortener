Scenario-Based
XYZ Pte Ltd is a company dealing with digital products and has about 800 million followers internationally which it regularly broadcasts news to via Twitter. They want to create a link shortener that allows them to maximise their understanding of their followers via statistics. Describe how you would advise them to proceed technically considering they can only provide a basic Linux-based server.
 
Please provide:
1. A list of your perceived requirements
2. A list of your derived technical requirements
3. Description of platform infrastructure/third party software including justifications
4. Description of business logic (including link shortening mechanism)
5. Any other clarifications
 
Remember to keep their business objectives in mind!





Answers:
-----------------------------------------------------------------------------------------------
To detract from the question, Twitter actually provides outbound link analytics as a service

https://analytics.twitter.com/about !


Perceived requirements:

The main objective of the client is to be able to understand their followers' attributes and behaviour after reading a tweet.

The client wants a simple proof-of-concept rather than a full blown implementation

Shortened URLs should be non-expiring

Shortened URLs should be as short as possible

Shortened URLs are write-once, read-often

Technical requirements:

The proof-of-concept has to maintain a one-one mapping of destination URLs to shortened keys

The proof-of-concept's design has to keep in mind high availability and redundancy but does not have to have that in implementation


Description of platform infrastructure:

The design will assume a (consistent hashing) distributed key-value store such as Amazon DynamoDB or Cassandra - reason being we don't need the relational properties, and DynamoDB is easier to scale.

Write-once, read-often has implications on the design of the system: split the URL generation endpoint from the redirection endpoint.

Analytics are done by mining the server access logs: should be much more performant than logging from the application server. Also decouples datamining from application serving



Business Logic:

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


