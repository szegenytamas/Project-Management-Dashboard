# Certificates

`rds-global-bundle.pem` – the certificate authorities (CAs) of Amazon RDS, all regions.

The API and the size-limit Lambda use it to **verify** the TLS certificate of the RDS
PostgreSQL server (`POSTGRES_SSL_MODE=verify-full`): the connection is encrypted *and* the
server is proven to be the real RDS instance, not someone in the middle.

These are public certificates, not secrets. Source and refresh (AWS rotates CAs rarely):

```bash
curl -o certs/rds-global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
```

Documentation: <https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html>
