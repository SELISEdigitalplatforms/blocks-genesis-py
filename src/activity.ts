import { Span, Tracer, context, trace } from "@opentelemetry/api";

export class Activity {
  private span: Span;

  constructor(private tracer: Tracer, name: string) {
    this.span = tracer.startSpan(name);
  }

  setProperty(key: string, value: any) {
    this.span.setAttribute(key, value);
  }

  setStatus(code: number, message?: string) {
    this.span.setStatus({ code, message });
  }

  end() {
    this.span.end();
  }
}
