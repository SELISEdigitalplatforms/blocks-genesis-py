import {
  trace,
  context,
} from "@opentelemetry/api";
import {
  NodeTracerProvider
} from "@opentelemetry/sdk-trace-node";
import {
  AzureMonitorTraceExporter
} from "@azure/monitor-opentelemetry-exporter";

export function configureTracing(opts: {
  blocksKey: string;
  serviceId: string;
  connectionString: string;
}) {
  const provider = new NodeTracerProvider();
  const exporter = new AzureMonitorTraceExporter({
    connectionString: opts.connectionString,
  });

  provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
  provider.register();

  return trace.getTracer(opts.serviceId);
}
