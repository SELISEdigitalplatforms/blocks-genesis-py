export function tracingMiddleware(tracer) {
  return function (req, res, next) {
    const span = tracer.startSpan(`HTTP ${req.method} ${req.path}`);
    res.on("finish", () => span.end());
    next();
  }
}
