# TODO: imagen para la prueba en contenedor limpio 
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["bash", "run.sh"]
