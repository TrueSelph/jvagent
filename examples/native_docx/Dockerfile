# Optional DOCX script dependencies; none are host harness requirements.
FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS node
WORKDIR /opt/jv-docx
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /opt/jv-docx/node_modules /opt/jv-docx/node_modules
ENV NODE_PATH=/opt/jv-docx/node_modules
RUN pip install --no-cache-dir defusedxml==0.7.1 lxml==6.1.3
