import winston from 'winston';
import { ServiceBusClient } from '@azure/service-bus';

export function configureLogger(opts: {
  blocksKey: string;
  serviceId: string;
  connectionString: string;
}) {
  const sbClient = new ServiceBusClient(opts.connectionString);
  const sender = sbClient.createSender("logs");

  const logger = winston.createLogger({
    level: "info",
    format: winston.format.json(),
    defaultMeta: {
      blocksKey: opts.blocksKey,
      serviceId: opts.serviceId,
    },
    transports: [
      new winston.transports.Console(),
      new winston.transports.Stream({
        stream: {
          write: (msg) => sender.sendMessages({ body: msg }),
        },
      }),
    ],
  });

  return logger;
}
