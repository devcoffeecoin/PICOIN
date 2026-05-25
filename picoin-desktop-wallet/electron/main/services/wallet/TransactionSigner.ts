import nacl from "tweetnacl";
import type { SignedTransaction, TransactionDraft } from "../../../../shared/types";
import { base64UrlEncode, canonicalJson, decodeEd25519Key, sha256Text } from "./encoding";

export interface TransactionSigner {
  sign(tx: TransactionDraft, privateKey: string, publicKey: string): SignedTransaction;
}

export class Ed25519TransactionSigner implements TransactionSigner {
  sign(tx: TransactionDraft, privateKey: string, publicKey: string): SignedTransaction {
    const privateSeed = decodeEd25519Key(privateKey);
    const keypair = nacl.sign.keyPair.fromSeed(privateSeed);
    const message = Buffer.from(canonicalJson(tx), "utf-8");
    const signature = nacl.sign.detached(message, keypair.secretKey);
    return {
      ...tx,
      public_key: publicKey,
      signature: base64UrlEncode(signature),
      tx_hash: sha256Text(canonicalJson({ public_key: publicKey, tx })),
    };
  }
}

