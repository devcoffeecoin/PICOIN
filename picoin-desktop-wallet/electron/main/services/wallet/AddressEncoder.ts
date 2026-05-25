import { sha256Text } from "./encoding";

export interface AddressEncoder {
  fromPublicKey(publicKey: string): string;
}

export class PicoinAddressEncoder implements AddressEncoder {
  fromPublicKey(publicKey: string): string {
    const body = sha256Text(publicKey).toUpperCase().slice(0, 38);
    const checksum = sha256Text(body).toUpperCase().slice(0, 8);
    return `PI${body}${checksum}`;
  }
}

