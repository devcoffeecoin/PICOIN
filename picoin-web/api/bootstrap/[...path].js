const { proxyHandler } = require("../_proxy");

module.exports = async function handler(req, res) {
  return proxyHandler(req, res, {
    name: "Picoin bootstrap API",
    target: process.env.PICOIN_BOOTSTRAP_API_URL || "https://api.picoin.science",
  });
};
