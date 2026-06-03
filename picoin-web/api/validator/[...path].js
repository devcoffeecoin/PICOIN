const { proxyHandler } = require("../_proxy");

module.exports = async function handler(req, res) {
  return proxyHandler(req, res, {
    name: "Picoin validator API",
    target: process.env.PICOIN_VALIDATOR_API_URL || "https://api.picoin.science",
  });
};
