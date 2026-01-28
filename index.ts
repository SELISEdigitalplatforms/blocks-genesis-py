import express from "express";
import { configureLogger, configureTracing, Activity } from "@selise/lmt-blocks";

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
