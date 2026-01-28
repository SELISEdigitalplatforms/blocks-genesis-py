# Selise Blocks LMT for nodejs

Selise Blocks LMT (Logging, Monitoring, and Tracing) library for Selise Blocks applications. It sends logs and traces to Azure Service Bus for centralized processing.

## Example usage in Node.js

```

import express from "express";
import { configureLogger, configureTracing, Activity } from "node-lmt-blocks";

const logger = configureLogger({
  blocksKey: "default-tenant",
  serviceId: "example-service",
  connectionString: process.env.AZURE_SB_CONN!,
});

const tracer = configureTracing({
  blocksKey: "default-tenant",
  serviceId: "example-service",
  connectionString: process.env.AZURE_SB_CONN!,
});

const app = express();
app.use(express.json());

app.get("/", (req, res) => {
  const activity = new Activity(tracer, "rootHandler");
  logger.info("Root called");
  activity.end();
  res.send({ message: "Hello World" });
});

app.listen(3000);
```