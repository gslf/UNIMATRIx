"use strict";
// Mutation requests carry a header checked by the server.
const unimatrixFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = new URL(input instanceof Request ? input.url : input, location.href);
  if (url.origin === location.origin) {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    headers.set("X-Unimatrix-Request", "1");
    init = { ...init, headers };
  }
  return unimatrixFetch(input, init);
};
