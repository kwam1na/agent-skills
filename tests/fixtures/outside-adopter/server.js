const http = require('node:http');
http.createServer((_request, response) => response.end('ok')).listen(3000);
