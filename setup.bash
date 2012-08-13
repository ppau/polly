#!/bin/bash

curl -O https://raw.github.com/gist/1b54b939e4ae7b7c08cc/5984e7112565f3e43067b639d97fd283887baa38/sio-test.py

mkdir static && cd static
curl -O http://backbonejs.org/backbone.js \
-O http://documentcloud.github.com/underscore/underscore.js \
-O https://raw.github.com/mrjoes/tornadio2/master/examples/socket.io.js \
-O https://raw.github.com/bbqsrc/backbone-socket.io/master/backbone-socket.io.js \
-O https://raw.github.com/gist/7487500ffddd6b1f0f45/a774e727e0bec342d4c4dc34d1d95f6ba1421a59/test.html
cd ..

