FROM nginx:1.28-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx . /usr/share/nginx/html/
RUN rm /usr/share/nginx/html/nginx.conf

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1/healthz || exit 1
