FROM nginx:alpine
COPY index.html wiki.css wiki.js scene.js /usr/share/nginx/html/
COPY vendor /usr/share/nginx/html/vendor
COPY examples /usr/share/nginx/html/examples
COPY deploy /usr/share/nginx/html/deploy
COPY WIKI.md openapi.json admin-openapi.json /usr/share/nginx/html/
