/* xCDN JavaScript 1.0.1 (https://github.com/gslf/xCDN-JavaScript), bundled as a classic
   script for the UNIMATRIx Explorer by explorer/tools/bundle_xcdn.py.

   MIT License

   Copyright (c) 2026 Gioele Stefano Luca Fierro

   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:

   The above copyright notice and this permission notice shall be included in all
   copies or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
   SOFTWARE.
*/
(function (root) {
"use strict";
// ---- error.js
/**
 * xCDN Error Module
 * Error handling for the xCDN parser
 */

/**
 * Represents a position in the source code
 */
class Span {
  /**
   * @param {number} offset - Absolute byte offset
   * @param {number} line - Line number (1-indexed)
   * @param {number} column - Column number (1-indexed)
   */
  constructor(offset, line, column) {
    this.offset = offset;
    this.line = line;
    this.column = column;
  }

  toString() {
    return `${this.line}:${this.column}`;
  }

  clone() {
    return new Span(this.offset, this.line, this.column);
  }
}

/**
 * Possible error types
 */
const ErrorKind = {
  Eof: 'Eof',
  InvalidToken: 'InvalidToken',
  Expected: 'Expected',
  InvalidEscape: 'InvalidEscape',
  InvalidNumber: 'InvalidNumber',
  InvalidDecimal: 'InvalidDecimal',
  InvalidDateTime: 'InvalidDateTime',
  InvalidDuration: 'InvalidDuration',
  InvalidUuid: 'InvalidUuid',
  InvalidBase64: 'InvalidBase64',
  Message: 'Message',
};

/**
 * xCDN error with position information
 */
class XCDNError extends Error {
  /**
   * @param {string} kind - Error type (from ErrorKind)
   * @param {Span} span - Error position
   * @param {string} [context] - Additional context
   */
  constructor(kind, span, context = null) {
    const message = XCDNError.formatMessage(kind, context, span);
    super(message);
    this.name = 'XCDNError';
    this.kind = kind;
    this.span = span;
    this.context = context;
  }

  static formatMessage(kind, context, span) {
    let msg;
    switch (kind) {
      case ErrorKind.Eof:
        msg = 'Unexpected end of input';
        break;
      case ErrorKind.InvalidToken:
        msg = `Invalid token: ${context}`;
        break;
      case ErrorKind.Expected:
        msg = context; // "Expected X, found Y"
        break;
      case ErrorKind.InvalidEscape:
        msg = 'Invalid escape sequence';
        break;
      case ErrorKind.InvalidNumber:
        msg = 'Invalid number format';
        break;
      case ErrorKind.InvalidDecimal:
        msg = `Invalid decimal value: ${context}`;
        break;
      case ErrorKind.InvalidDateTime:
        msg = `Invalid datetime value: ${context}`;
        break;
      case ErrorKind.InvalidDuration:
        msg = `Invalid duration value: ${context}`;
        break;
      case ErrorKind.InvalidUuid:
        msg = `Invalid UUID value: ${context}`;
        break;
      case ErrorKind.InvalidBase64:
        msg = `Invalid base64 value: ${context}`;
        break;
      case ErrorKind.Message:
        msg = context;
        break;
      default:
        msg = context || 'Unknown error';
    }
    return `${msg} at ${span.toString()}`;
  }
}

// ---- ast.js
/**
 * xCDN AST Module
 * Data structures for the xCDN format
 */

/**
 * Main xCDN document
 */
class Document {
  /**
   * @param {Directive[]} prolog - Initial directives ($schema, etc.)
   * @param {Node[]} values - Top-level values
   */
  constructor(prolog = [], values = []) {
    this.prolog = prolog;
    this.values = values;
  }

  /**
   * Dict-like access
   * @param {number|string} key
   */
  get(key) {
    if (typeof key === 'number') {
      return this.values[key];
    }
    // Access the first object
    if (this.values.length > 0 && this.values[0].value instanceof XObject) {
      return this.values[0].value.get(key);
    }
    return undefined;
  }

  /**
   * Set dict-like
   * @param {string} key
   * @param {*} value
   */
  set(key, value) {
    if (this.values.length > 0 && this.values[0].value instanceof XObject) {
      this.values[0].value.set(key, value);
    }
  }

  /**
   * Check if key exists
   * @param {string} key
   */
  has(key) {
    if (this.values.length > 0 && this.values[0].value instanceof XObject) {
      return this.values[0].value.has(key);
    }
    return false;
  }

  /**
   * Unwrap to raw JS value (delegates to first value's unwrap for implicit-object documents)
   */
  unwrap() {
    if (this.values.length > 0) {
      return this.values[0].unwrap();
    }
    return undefined;
  }
}

/**
 * Directive ($name: value)
 */
class Directive {
  /**
   * @param {string} name - Directive name (without $)
   * @param {*} value - Directive value
   */
  constructor(name, value) {
    this.name = name;
    this.value = value;
  }
}

/**
 * Node decorated with tags and annotations
 */
class Node {
  /**
   * @param {Tag[]} tags - List of tags (#tag)
   * @param {Annotation[]} annotations - List of annotations (@anno)
   * @param {*} value - Actual value
   */
  constructor(tags = [], annotations = [], value = null) {
    this.tags = tags;
    this.annotations = annotations;
    this.value = value;
  }

  // Dict-like methods delegated to the value
  get(key, defaultValue = undefined) {
    if (this.value && typeof this.value.get === 'function') {
      return this.value.get(key, defaultValue);
    }
    return defaultValue;
  }

  set(key, value) {
    if (this.value && typeof this.value.set === 'function') {
      this.value.set(key, value);
    }
  }

  has(key) {
    if (this.value && typeof this.value.has === 'function') {
      return this.value.has(key);
    }
    return false;
  }

  keys() {
    if (this.value instanceof XObject) {
      return this.value.keys();
    }
    return [];
  }

  valuesIter() {
    if (this.value instanceof XObject) {
      return this.value.valuesIter();
    }
    return [];
  }

  items() {
    if (this.value instanceof XObject) {
      return this.value.items();
    }
    return [];
  }

  append(value) {
    if (this.value instanceof XArray) {
      this.value.append(value);
    }
  }

  get length() {
    if (this.value && typeof this.value.length !== 'undefined') {
      return this.value.length;
    }
    return 0;
  }

  [Symbol.iterator]() {
    if (this.value && typeof this.value[Symbol.iterator] === 'function') {
      return this.value[Symbol.iterator]();
    }
    return [][Symbol.iterator]();
  }

  /**
   * Unwrap to raw JS value (delegates to this.value.unwrap())
   */
  unwrap() {
    if (this.value && typeof this.value.unwrap === 'function') {
      return this.value.unwrap();
    }
    return this.value;
  }
}

/**
 * Tag (#tag)
 */
class Tag {
  /**
   * @param {string} name - Tag name
   */
  constructor(name) {
    this.name = name;
  }
}

/**
 * Annotation (@name(args...))
 */
class Annotation {
  /**
   * @param {string} name - Annotation name
   * @param {*[]} args - Optional arguments
   */
  constructor(name, args = []) {
    this.name = name;
    this.args = args;
  }
}

// ============== VALUE TYPES ==============

/**
 * Base class for value types
 */
class ValueType {
  constructor() {
    if (new.target === ValueType) {
      throw new Error('ValueType is abstract');
    }
  }
}

/**
 * Null value
 */
class Null extends ValueType {
  constructor() {
    super();
  }

  unwrap() {
    return null;
  }
}

/**
 * Boolean value
 */
class Bool extends ValueType {
  /**
   * @param {boolean} value
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Integer value
 */
class Int extends ValueType {
  /**
   * @param {number|bigint} value
   */
  constructor(value) {
    super();
    this.value = typeof value === 'bigint' ? value : BigInt(Math.trunc(value));
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Float value
 */
class Float extends ValueType {
  /**
   * @param {number} value
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Decimal value (d"...")
 */
class DecimalValue extends ValueType {
  /**
   * @param {string} value - String representation of the decimal
   */
  constructor(value) {
    super();
    this.value = value; // Keep as string for precision
  }

  unwrap() {
    return this.value;
  }
}

/**
 * String value
 */
class XString extends ValueType {
  /**
   * @param {string} value
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Bytes value (b"..." base64)
 */
class Bytes extends ValueType {
  /**
   * @param {Uint8Array} value - Decoded bytes
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Datetime value (t"...")
 */
class DateTime extends ValueType {
  /**
   * @param {Date} value
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * Duration value (r"...")
 */
class Duration extends ValueType {
  /**
   * @param {string} value - ISO8601 duration as string
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * UUID value (u"...")
 */
class Uuid extends ValueType {
  /**
   * @param {string} value - UUID as string
   */
  constructor(value) {
    super();
    this.value = value;
  }

  unwrap() {
    return this.value;
  }
}

/**
 * xCDN Array
 */
class XArray extends ValueType {
  /**
   * @param {Node[]} value - List of nodes
   */
  constructor(value = []) {
    super();
    this.value = value;
  }

  get(index) {
    return this.value[index];
  }

  set(index, val) {
    this.value[index] = val;
  }

  get length() {
    return this.value.length;
  }

  append(val) {
    // Wrap in Node if not already
    if (val instanceof Node) {
      this.value.push(val);
    } else {
      this.value.push(new Node([], [], val));
    }
  }

  unwrap() {
    return this.value.map(node => node.unwrap());
  }

  [Symbol.iterator]() {
    return this.value[Symbol.iterator]();
  }
}

/**
 * xCDN Object
 */
class XObject extends ValueType {
  /**
   * @param {Map<string, Node>|Object} value - Key-value map
   */
  constructor(value = new Map()) {
    super();
    if (value instanceof Map) {
      this.value = value;
    } else {
      this.value = new Map(Object.entries(value));
    }
  }

  get(key, defaultValue = undefined) {
    return this.value.has(key) ? this.value.get(key) : defaultValue;
  }

  set(key, val) {
    // Wrap in Node if not already
    if (val instanceof Node) {
      this.value.set(key, val);
    } else {
      this.value.set(key, new Node([], [], val));
    }
  }

  has(key) {
    return this.value.has(key);
  }

  get length() {
    return this.value.size;
  }

  keys() {
    return Array.from(this.value.keys());
  }

  valuesIter() {
    return Array.from(this.value.values());
  }

  items() {
    return Array.from(this.value.entries());
  }

  unwrap() {
    const result = {};
    for (const [key, node] of this.value.entries()) {
      result[key] = node.unwrap();
    }
    return result;
  }

  [Symbol.iterator]() {
    return this.value.keys()[Symbol.iterator]();
  }
}

// Export aliases for compatibility

// ---- lexer.js
/**
 * xCDN Lexer Module
 * Tokenization for the xCDN format
 */


/**
 * Token types
 */
const TokenType = {
  // Structure
  LBRACE: 'LBRACE',         // {
  RBRACE: 'RBRACE',         // }
  LBRACKET: 'LBRACKET',     // [
  RBRACKET: 'RBRACKET',     // ]
  LPAREN: 'LPAREN',         // (
  RPAREN: 'RPAREN',         // )
  COLON: 'COLON',           // :
  COMMA: 'COMMA',           // ,
  DOLLAR: 'DOLLAR',         // $
  HASH: 'HASH',             // #
  AT: 'AT',                 // @

  // Keywords
  TRUE: 'TRUE',
  FALSE: 'FALSE',
  NULL: 'NULL',

  // Values
  IDENT: 'IDENT',           // Unquoted identifiers
  INT: 'INT',               // Integer numbers
  FLOAT: 'FLOAT',           // Floating point numbers
  STRING: 'STRING',         // Strings "..."
  TRIPLE_STRING: 'TRIPLE_STRING', // Strings """..."""

  // Typed strings
  D_QUOTED: 'D_QUOTED',     // d"decimal"
  B_QUOTED: 'B_QUOTED',     // b"base64"
  U_QUOTED: 'U_QUOTED',     // u"uuid"
  T_QUOTED: 'T_QUOTED',     // t"datetime"
  R_QUOTED: 'R_QUOTED',     // r"duration"

  // End of input
  EOF: 'EOF',
};

/**
 * Single token
 */
class Token {
  /**
   * @param {string} kind - Token type (from TokenType)
   * @param {Span} span - Position in source
   * @param {*} value - Extracted value (optional)
   */
  constructor(kind, span, value = null) {
    this.kind = kind;
    this.span = span;
    this.value = value;
  }
}

/**
 * Lexer for xCDN
 */
class Lexer {
  /**
   * @param {string} source - Source code
   */
  constructor(source) {
    this.source = source;
    this.pos = 0;
    this.line = 1;
    this.column = 1;
  }

  /**
   * Creates a Span for the current position
   * @returns {Span}
   */
  span() {
    return new Span(this.pos, this.line, this.column);
  }

  /**
   * Reads the next character without consuming it
   * @returns {string|null}
   */
  peek() {
    if (this.pos >= this.source.length) {
      return null;
    }
    return this.source[this.pos];
  }

  /**
   * Looks at the second character ahead without consuming
   * @returns {string|null}
   */
  peekNext() {
    if (this.pos + 1 >= this.source.length) {
      return null;
    }
    return this.source[this.pos + 1];
  }

  /**
   * Consumes and returns the next character
   * @returns {string|null}
   */
  bump() {
    if (this.pos >= this.source.length) {
      return null;
    }
    const ch = this.source[this.pos];
    this.pos++;
    if (ch === '\n') {
      this.line++;
      this.column = 1;
    } else {
      this.column++;
    }
    return ch;
  }

  /**
   * Skips whitespace and comments
   */
  skipWsAndComments() {
    while (true) {
      const ch = this.peek();
      if (ch === null) break;

      // Whitespace
      if (ch === ' ' || ch === '\t' || ch === '\r' || ch === '\n') {
        this.bump();
        continue;
      }

      // Comments
      if (ch === '/') {
        const next = this.peekNext();
        if (next === '/') {
          // Line comment
          this.bump(); // /
          this.bump(); // /
          while (this.peek() !== null && this.peek() !== '\n') {
            this.bump();
          }
          continue;
        } else if (next === '*') {
          // Block comment
          this.bump(); // /
          this.bump(); // *
          while (true) {
            const c = this.bump();
            if (c === null) {
              throw new XCDNError(ErrorKind.Eof, this.span(), 'Unterminated block comment');
            }
            if (c === '*' && this.peek() === '/') {
              this.bump(); // /
              break;
            }
          }
          continue;
        }
      }

      break;
    }
  }

  /**
   * Reads an identifier or keyword
   * @returns {Token}
   */
  readIdent() {
    const startSpan = this.span();
    let value = '';

    while (true) {
      const ch = this.peek();
      if (ch === null) break;
      if (this.isIdentContinue(ch)) {
        value += this.bump();
      } else {
        break;
      }
    }

    // Check keywords
    if (value === 'true') {
      return new Token(TokenType.TRUE, startSpan, true);
    } else if (value === 'false') {
      return new Token(TokenType.FALSE, startSpan, false);
    } else if (value === 'null') {
      return new Token(TokenType.NULL, startSpan, null);
    }

    return new Token(TokenType.IDENT, startSpan, value);
  }

  /**
   * Checks if the character can start an identifier
   * @param {string} ch
   * @returns {boolean}
   */
  isIdentStart(ch) {
    return (ch >= 'a' && ch <= 'z') ||
           (ch >= 'A' && ch <= 'Z') ||
           ch === '_';
  }

  /**
   * Checks if the character can continue an identifier
   * @param {string} ch
   * @returns {boolean}
   */
  isIdentContinue(ch) {
    return this.isIdentStart(ch) ||
           (ch >= '0' && ch <= '9') ||
           ch === '-';
  }

  /**
   * Reads a number (integer or float)
   * @returns {Token}
   */
  readNumber() {
    const startSpan = this.span();
    let value = '';
    let isFloat = false;

    // Optional sign
    if (this.peek() === '+' || this.peek() === '-') {
      value += this.bump();
    }

    // Integer part
    while (this.peek() !== null && this.peek() >= '0' && this.peek() <= '9') {
      value += this.bump();
    }

    // Decimal part
    if (this.peek() === '.' && this.peekNext() >= '0' && this.peekNext() <= '9') {
      isFloat = true;
      value += this.bump(); // .
      while (this.peek() !== null && this.peek() >= '0' && this.peek() <= '9') {
        value += this.bump();
      }
    }

    // Exponent
    if (this.peek() === 'e' || this.peek() === 'E') {
      isFloat = true;
      value += this.bump(); // e/E
      if (this.peek() === '+' || this.peek() === '-') {
        value += this.bump();
      }
      while (this.peek() !== null && this.peek() >= '0' && this.peek() <= '9') {
        value += this.bump();
      }
    }

    if (isFloat) {
      const floatVal = parseFloat(value);
      if (isNaN(floatVal)) {
        throw new XCDNError(ErrorKind.InvalidNumber, startSpan, value);
      }
      return new Token(TokenType.FLOAT, startSpan, floatVal);
    } else {
      // Try BigInt for large numbers
      try {
        const intVal = BigInt(value);
        return new Token(TokenType.INT, startSpan, intVal);
      } catch {
        throw new XCDNError(ErrorKind.InvalidNumber, startSpan, value);
      }
    }
  }

  /**
   * Reads a normal string "..."
   * @returns {string}
   */
  readStringContent() {
    let value = '';

    while (true) {
      const ch = this.bump();
      if (ch === null) {
        throw new XCDNError(ErrorKind.Eof, this.span(), 'Unterminated string');
      }
      if (ch === '"') {
        break;
      }
      if (ch === '\\') {
        const escaped = this.bump();
        if (escaped === null) {
          throw new XCDNError(ErrorKind.Eof, this.span(), 'Unterminated escape sequence');
        }
        switch (escaped) {
          case '"': value += '"'; break;
          case '\\': value += '\\'; break;
          case '/': value += '/'; break;
          case 'b': value += '\b'; break;
          case 'f': value += '\f'; break;
          case 'n': value += '\n'; break;
          case 'r': value += '\r'; break;
          case 't': value += '\t'; break;
          case 'u': {
            // Unicode escape \uXXXX
            let hex = '';
            for (let i = 0; i < 4; i++) {
              const h = this.bump();
              if (h === null || !this.isHexDigit(h)) {
                throw new XCDNError(ErrorKind.InvalidEscape, this.span(), '\\u requires 4 hex digits');
              }
              hex += h;
            }
            value += String.fromCharCode(parseInt(hex, 16));
            break;
          }
          default:
            throw new XCDNError(ErrorKind.InvalidEscape, this.span(), `Unknown escape: \\${escaped}`);
        }
      } else {
        value += ch;
      }
    }

    return value;
  }

  /**
   * Checks if the character is a hexadecimal digit
   * @param {string} ch
   * @returns {boolean}
   */
  isHexDigit(ch) {
    return (ch >= '0' && ch <= '9') ||
           (ch >= 'a' && ch <= 'f') ||
           (ch >= 'A' && ch <= 'F');
  }

  /**
   * Reads a triple string """..."""
   * @returns {string}
   */
  readTripleString() {
    let value = '';

    while (true) {
      const ch = this.bump();
      if (ch === null) {
        throw new XCDNError(ErrorKind.Eof, this.span(), 'Unterminated triple-quoted string');
      }
      if (ch === '"' && this.peek() === '"' && this.peekNext() === '"') {
        this.bump(); // "
        this.bump(); // "
        break;
      }
      value += ch;
    }

    return value;
  }

  /**
   * Reads the next token
   * @returns {Token}
   */
  nextToken() {
    this.skipWsAndComments();

    const startSpan = this.span();
    const ch = this.peek();

    if (ch === null) {
      return new Token(TokenType.EOF, startSpan);
    }

    // Single structural characters
    switch (ch) {
      case '{':
        this.bump();
        return new Token(TokenType.LBRACE, startSpan);
      case '}':
        this.bump();
        return new Token(TokenType.RBRACE, startSpan);
      case '[':
        this.bump();
        return new Token(TokenType.LBRACKET, startSpan);
      case ']':
        this.bump();
        return new Token(TokenType.RBRACKET, startSpan);
      case '(':
        this.bump();
        return new Token(TokenType.LPAREN, startSpan);
      case ')':
        this.bump();
        return new Token(TokenType.RPAREN, startSpan);
      case ':':
        this.bump();
        return new Token(TokenType.COLON, startSpan);
      case ',':
        this.bump();
        return new Token(TokenType.COMMA, startSpan);
      case '$':
        this.bump();
        return new Token(TokenType.DOLLAR, startSpan);
      case '#':
        this.bump();
        return new Token(TokenType.HASH, startSpan);
      case '@':
        this.bump();
        return new Token(TokenType.AT, startSpan);
    }

    // Strings
    if (ch === '"') {
      this.bump(); // opening "

      // Check for triple-quoted string
      if (this.peek() === '"' && this.peekNext() === '"') {
        this.bump(); // "
        this.bump(); // "
        const value = this.readTripleString();
        return new Token(TokenType.TRIPLE_STRING, startSpan, value);
      }

      const value = this.readStringContent();
      return new Token(TokenType.STRING, startSpan, value);
    }

    // Typed strings (d", b", u", t", r")
    if ((ch === 'd' || ch === 'b' || ch === 'u' || ch === 't' || ch === 'r') &&
        this.peekNext() === '"') {
      const prefix = this.bump();
      this.bump(); // "
      const value = this.readStringContent();

      switch (prefix) {
        case 'd': return new Token(TokenType.D_QUOTED, startSpan, value);
        case 'b': return new Token(TokenType.B_QUOTED, startSpan, value);
        case 'u': return new Token(TokenType.U_QUOTED, startSpan, value);
        case 't': return new Token(TokenType.T_QUOTED, startSpan, value);
        case 'r': return new Token(TokenType.R_QUOTED, startSpan, value);
      }
    }

    // Identifiers
    if (this.isIdentStart(ch)) {
      return this.readIdent();
    }

    // Numbers
    if ((ch >= '0' && ch <= '9') ||
        ch === '.' ||
        ch === '+' ||
        ch === '-') {
      // Verify it's a valid number
      if (ch === '.' && !(this.peekNext() >= '0' && this.peekNext() <= '9')) {
        throw new XCDNError(ErrorKind.InvalidToken, startSpan, ch);
      }
      if ((ch === '+' || ch === '-') && !(this.peekNext() >= '0' && this.peekNext() <= '9') && this.peekNext() !== '.') {
        throw new XCDNError(ErrorKind.InvalidToken, startSpan, ch);
      }
      return this.readNumber();
    }

    throw new XCDNError(ErrorKind.InvalidToken, startSpan, ch);
  }
}

// ---- parser.js
/**
 * xCDN Parser Module
 * Recursive descent parser for the xCDN format
 */




/**
 * Decode Base64 (standard or URL-safe)
 * @param {string} str
 * @returns {Uint8Array}
 */
function decodeBase64(str) {
  try {
    // Try standard base64
    const binary = atob(str);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  } catch {
    // Try URL-safe base64
    try {
      const urlSafe = str.replace(/-/g, '+').replace(/_/g, '/');
      // Add padding if needed
      const padded = urlSafe + '='.repeat((4 - urlSafe.length % 4) % 4);
      const binary = atob(padded);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes;
    } catch {
      throw new Error(`Invalid base64: ${str}`);
    }
  }
}

/**
 * Validates and parses UUID
 * @param {string} str
 * @returns {string}
 */
function parseUuid(str) {
  const uuidRegex = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
  if (!uuidRegex.test(str)) {
    throw new Error(`Invalid UUID: ${str}`);
  }
  return str.toLowerCase();
}

/**
 * Parses RFC3339 datetime
 * @param {string} str
 * @returns {Date}
 */
function parseDateTime(str) {
  // Replace Z with +00:00 for compatibility
  const normalized = str.replace('Z', '+00:00');
  const date = new Date(normalized);
  if (isNaN(date.getTime())) {
    throw new Error(`Invalid datetime: ${str}`);
  }
  return date;
}

/**
 * xCDN Parser
 */
class Parser {
  /**
   * @param {string} source - Source code
   */
  constructor(source) {
    this.lexer = new Lexer(source);
    this.current = this.lexer.nextToken();
  }

  /**
   * Returns the current token without consuming it
   * @returns {Token}
   */
  peek() {
    return this.current;
  }

  /**
   * Consumes and returns the current token
   * @returns {Token}
   */
  bump() {
    const token = this.current;
    this.current = this.lexer.nextToken();
    return token;
  }

  /**
   * Verifies and consumes a specific token
   * @param {string} kind - Expected type
   * @returns {Token}
   */
  expect(kind) {
    const token = this.peek();
    if (token.kind !== kind) {
      throw new XCDNError(
        ErrorKind.Expected,
        token.span,
        `Expected ${kind}, found ${token.kind}`
      );
    }
    return this.bump();
  }

  /**
   * Parses an identifier (IDENT or STRING)
   * @returns {string}
   */
  parseIdentString() {
    const token = this.peek();
    if (token.kind === TokenType.IDENT) {
      this.bump();
      return token.value;
    } else if (token.kind === TokenType.STRING) {
      this.bump();
      return token.value;
    } else {
      throw new XCDNError(
        ErrorKind.Expected,
        token.span,
        `Expected identifier or string, found ${token.kind}`
      );
    }
  }

  /**
   * Parses a key (for objects)
   * @returns {string}
   */
  parseKey() {
    return this.parseIdentString();
  }

  /**
   * Parses the complete document
   * @returns {Document}
   */
  parseDocument() {
    const prolog = [];
    const values = [];

    // Parse prolog directives ($name: value)
    while (this.peek().kind === TokenType.DOLLAR) {
      this.bump(); // $
      const name = this.parseIdentString();
      this.expect(TokenType.COLON);
      const value = this.parseNode();
      prolog.push(new Directive(name, value));
      if (this.peek().kind === TokenType.COMMA) {
        this.bump();
      }
    }

    // Detect implicit object vs stream
    if (this.peek().kind !== TokenType.EOF) {
      const isImplicitObject = this.isImplicitObject();

      if (isImplicitObject) {
        // Parse implicit object
        const objMap = new Map();
        while (this.peek().kind !== TokenType.EOF) {
          const key = this.parseKey();
          this.expect(TokenType.COLON);
          const node = this.parseNode();
          objMap.set(key, node);
          if (this.peek().kind === TokenType.COMMA) {
            this.bump();
          }
        }
        values.push(new Node([], [], new XObject(objMap)));
      } else {
        // Parse stream of values
        while (this.peek().kind !== TokenType.EOF) {
          const node = this.parseNode();
          values.push(node);
        }
      }
    }

    return new Document(prolog, values);
  }

  /**
   * Determines if the top-level is an implicit object
   * @returns {boolean}
   */
  isImplicitObject() {
    // If the next token is IDENT or STRING, check if it's followed by COLON
    const token = this.peek();
    if (token.kind === TokenType.IDENT || token.kind === TokenType.STRING) {
      // Save state
      const savedPos = this.lexer.pos;
      const savedLine = this.lexer.line;
      const savedColumn = this.lexer.column;
      const savedCurrent = this.current;

      // Consume and check
      this.bump();
      const next = this.peek();
      const isObject = next.kind === TokenType.COLON;

      // Restore state
      this.lexer.pos = savedPos;
      this.lexer.line = savedLine;
      this.lexer.column = savedColumn;
      this.current = savedCurrent;

      return isObject;
    }
    return false;
  }

  /**
   * Parses a node (with decorations)
   * @returns {Node}
   */
  parseNode() {
    const tags = [];
    const annotations = [];

    // Parse decorations
    while (true) {
      if (this.peek().kind === TokenType.AT) {
        this.bump(); // @
        const name = this.parseIdentString();
        const args = [];
        if (this.peek().kind === TokenType.LPAREN) {
          this.bump(); // (
          if (this.peek().kind !== TokenType.RPAREN) {
            while (true) {
              const val = this.parseValue();
              args.push(val);
              if (this.peek().kind === TokenType.COMMA) {
                this.bump();
              } else if (this.peek().kind === TokenType.RPAREN) {
                break;
              } else {
                throw new XCDNError(
                  ErrorKind.Expected,
                  this.peek().span,
                  `Expected , or ), found ${this.peek().kind}`
                );
              }
            }
          }
          this.expect(TokenType.RPAREN);
        }
        annotations.push(new Annotation(name, args));
      } else if (this.peek().kind === TokenType.HASH) {
        this.bump(); // #
        const name = this.parseIdentString();
        tags.push(new Tag(name));
      } else {
        break;
      }
    }

    const value = this.parseValue();
    return new Node(tags, annotations, value);
  }

  /**
   * Parses a value
   * @returns {*}
   */
  parseValue() {
    const token = this.peek();

    switch (token.kind) {
      case TokenType.LBRACE:
        return this.parseObject();

      case TokenType.LBRACKET:
        return this.parseArray();

      case TokenType.STRING:
      case TokenType.TRIPLE_STRING:
        this.bump();
        return new XString(token.value);

      case TokenType.TRUE:
        this.bump();
        return new Bool(true);

      case TokenType.FALSE:
        this.bump();
        return new Bool(false);

      case TokenType.NULL:
        this.bump();
        return new Null();

      case TokenType.INT:
        this.bump();
        return new Int(token.value);

      case TokenType.FLOAT:
        this.bump();
        return new Float(token.value);

      case TokenType.D_QUOTED:
        this.bump();
        return new DecimalValue(token.value);

      case TokenType.B_QUOTED:
        this.bump();
        try {
          const bytes = decodeBase64(token.value);
          return new Bytes(bytes);
        } catch (e) {
          throw new XCDNError(ErrorKind.InvalidBase64, token.span, token.value);
        }

      case TokenType.U_QUOTED:
        this.bump();
        try {
          const uuid = parseUuid(token.value);
          return new Uuid(uuid);
        } catch (e) {
          throw new XCDNError(ErrorKind.InvalidUuid, token.span, token.value);
        }

      case TokenType.T_QUOTED:
        this.bump();
        try {
          const dt = parseDateTime(token.value);
          return new DateTime(dt);
        } catch (e) {
          throw new XCDNError(ErrorKind.InvalidDateTime, token.span, token.value);
        }

      case TokenType.R_QUOTED:
        this.bump();
        return new Duration(token.value);

      case TokenType.EOF:
        throw new XCDNError(ErrorKind.Eof, token.span, 'Unexpected end of input');

      default:
        throw new XCDNError(
          ErrorKind.InvalidToken,
          token.span,
          `Unexpected token: ${token.kind}`
        );
    }
  }

  /**
   * Parses an object {key: value, ...}
   * @returns {XObject}
   */
  parseObject() {
    this.expect(TokenType.LBRACE);
    const objMap = new Map();

    while (this.peek().kind !== TokenType.RBRACE) {
      const key = this.parseKey();
      this.expect(TokenType.COLON);
      const node = this.parseNode();
      objMap.set(key, node);

      if (this.peek().kind === TokenType.COMMA) {
        this.bump();
      }
    }

    this.expect(TokenType.RBRACE);
    return new XObject(objMap);
  }

  /**
   * Parses an array [item, ...]
   * @returns {XArray}
   */
  parseArray() {
    this.expect(TokenType.LBRACKET);
    const items = [];

    while (this.peek().kind !== TokenType.RBRACKET) {
      const node = this.parseNode();
      items.push(node);

      if (this.peek().kind === TokenType.COMMA) {
        this.bump();
      }
    }

    this.expect(TokenType.RBRACKET);
    return new XArray(items);
  }
}

/**
 * Parses an xCDN string
 * @param {string} source - Source code
 * @returns {Document}
 */
function parseStr(source) {
  const parser = new Parser(source);
  return parser.parseDocument();
}

/**
 * Parses from a reader (Python compatibility)
 * @param {{read: () => string}} reader
 * @returns {Document}
 */
function parseReader(reader) {
  const source = reader.read();
  return parseStr(source);
}

// ---- serializer.js
/**
 * xCDN Serializer Module
 * Serialization for the xCDN format
 */


/**
 * Serialization format
 */
class Format {
  /**
   * @param {boolean} pretty - Pretty-print with indentation
   * @param {number} indent - Spaces per indentation level
   * @param {boolean} trailingCommas - Trailing commas
   */
  constructor(pretty = true, indent = 2, trailingCommas = true) {
    this.pretty = pretty;
    this.indent = indent;
    this.trailingCommas = trailingCommas;
  }
}

/**
 * xCDN Serializer
 */
class Serializer {
  /**
   * @param {Format} format
   */
  constructor(format = new Format()) {
    this.format = format;
    this.output = '';
    this.depth = 0;
  }

  /**
   * Writes a string to the output
   * @param {string} str
   */
  write(str) {
    this.output += str;
  }

  /**
   * Writes a newline (if pretty)
   */
  newline() {
    if (this.format.pretty) {
      this.write('\n');
    }
  }

  /**
   * Writes the current indentation (if pretty)
   */
  writeIndent() {
    if (this.format.pretty) {
      this.write(' '.repeat(this.depth * this.format.indent));
    }
  }

  /**
   * Checks if a string is a simple identifier
   * @param {string} str
   * @returns {boolean}
   */
  isSimpleIdent(str) {
    if (!str || str.length === 0) return false;

    const first = str[0];
    if (!((first >= 'a' && first <= 'z') ||
          (first >= 'A' && first <= 'Z') ||
          first === '_')) {
      return false;
    }

    for (let i = 1; i < str.length; i++) {
      const ch = str[i];
      if (!((ch >= 'a' && ch <= 'z') ||
            (ch >= 'A' && ch <= 'Z') ||
            (ch >= '0' && ch <= '9') ||
            ch === '_' ||
            ch === '-')) {
        return false;
      }
    }

    return true;
  }

  /**
   * Escapes a string
   * @param {string} str
   * @returns {string}
   */
  escapeString(str) {
    let result = '';
    for (const ch of str) {
      switch (ch) {
        case '"': result += '\\"'; break;
        case '\\': result += '\\\\'; break;
        case '\b': result += '\\b'; break;
        case '\f': result += '\\f'; break;
        case '\n': result += '\\n'; break;
        case '\r': result += '\\r'; break;
        case '\t': result += '\\t'; break;
        default:
          const code = ch.charCodeAt(0);
          if (code < 0x20) {
            // Control character
            result += '\\u' + code.toString(16).padStart(4, '0');
          } else {
            result += ch;
          }
      }
    }
    return result;
  }

  /**
   * Encode bytes in base64
   * @param {Uint8Array} bytes
   * @returns {string}
   */
  encodeBase64(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  /**
   * Formats datetime to RFC3339
   * @param {Date} date
   * @returns {string}
   */
  formatDateTime(date) {
    return date.toISOString();
  }

  /**
   * Serializes a document
   * @param {Document} doc
   * @returns {string}
   */
  serialize(doc) {
    // Prolog
    for (const directive of doc.prolog) {
      this.serializeDirective(directive);
    }

    // Values
    for (let i = 0; i < doc.values.length; i++) {
      const node = doc.values[i];
      this.serializeNode(node, true);
      if (i < doc.values.length - 1) {
        this.newline();
      }
    }

    return this.output;
  }

  /**
   * Serializes a directive
   * @param {Directive} directive
   */
  serializeDirective(directive) {
    this.write('$');
    this.write(directive.name);
    this.write(':');
    if (this.format.pretty) this.write(' ');
    this.serializeNode(directive.value, false);
    if (this.format.trailingCommas) {
      this.write(',');
    }
    this.newline();
  }

  /**
   * Serializes a node
   * @param {Node} node
   * @param {boolean} isTopLevel
   */
  serializeNode(node, isTopLevel = false) {
    // Annotations
    for (const anno of node.annotations) {
      this.write('@');
      this.write(anno.name);
      if (anno.args.length > 0) {
        this.write('(');
        for (let i = 0; i < anno.args.length; i++) {
          this.serializeValue(anno.args[i]);
          if (i < anno.args.length - 1) {
            this.write(', ');
          }
        }
        this.write(')');
      }
      this.write(' ');
    }

    // Tags
    for (const tag of node.tags) {
      this.write('#');
      this.write(tag.name);
      this.write(' ');
    }

    // Value
    this.serializeValue(node.value);
  }

  /**
   * Serializes a value
   * @param {*} value
   */
  serializeValue(value) {
    if (value instanceof Null) {
      this.write('null');
    } else if (value instanceof Bool) {
      this.write(value.value ? 'true' : 'false');
    } else if (value instanceof Int) {
      this.write(value.value.toString());
    } else if (value instanceof Float) {
      this.write(value.value.toString());
    } else if (value instanceof DecimalValue) {
      this.write('d"');
      this.write(value.value);
      this.write('"');
    } else if (value instanceof XString) {
      this.write('"');
      this.write(this.escapeString(value.value));
      this.write('"');
    } else if (value instanceof Bytes) {
      this.write('b"');
      this.write(this.encodeBase64(value.value));
      this.write('"');
    } else if (value instanceof DateTime) {
      this.write('t"');
      this.write(this.formatDateTime(value.value));
      this.write('"');
    } else if (value instanceof Duration) {
      this.write('r"');
      this.write(value.value);
      this.write('"');
    } else if (value instanceof Uuid) {
      this.write('u"');
      this.write(value.value);
      this.write('"');
    } else if (value instanceof XArray) {
      this.serializeArray(value);
    } else if (value instanceof XObject) {
      this.serializeObject(value);
    } else {
      throw new Error(`Unknown value type: ${value?.constructor?.name}`);
    }
  }

  /**
   * Serializes an array
   * @param {XArray} arr
   */
  serializeArray(arr) {
    this.write('[');

    if (arr.value.length > 0) {
      if (this.format.pretty) {
        this.newline();
        this.depth++;
      }

      for (let i = 0; i < arr.value.length; i++) {
        if (this.format.pretty) {
          this.writeIndent();
        }
        this.serializeNode(arr.value[i]);

        if (i < arr.value.length - 1) {
          this.write(',');
        } else if (this.format.trailingCommas) {
          this.write(',');
        }

        if (this.format.pretty) {
          this.newline();
        }
      }

      if (this.format.pretty) {
        this.depth--;
        this.writeIndent();
      }
    }

    this.write(']');
  }

  /**
   * Serializes an object
   * @param {XObject} obj
   */
  serializeObject(obj) {
    this.write('{');

    const entries = Array.from(obj.value.entries());

    if (entries.length > 0) {
      if (this.format.pretty) {
        this.newline();
        this.depth++;
      }

      for (let i = 0; i < entries.length; i++) {
        const [key, node] = entries[i];

        if (this.format.pretty) {
          this.writeIndent();
        }

        // Key
        if (this.isSimpleIdent(key)) {
          this.write(key);
        } else {
          this.write('"');
          this.write(this.escapeString(key));
          this.write('"');
        }

        this.write(':');
        if (this.format.pretty) this.write(' ');

        // Value
        this.serializeNode(node);

        if (i < entries.length - 1) {
          this.write(',');
        } else if (this.format.trailingCommas) {
          this.write(',');
        }

        if (this.format.pretty) {
          this.newline();
        }
      }

      if (this.format.pretty) {
        this.depth--;
        this.writeIndent();
      }
    }

    this.write('}');
  }
}

/**
 * Serializes a document in pretty format
 * @param {Document} doc
 * @returns {string}
 */
function toStringPretty(doc) {
  const serializer = new Serializer(new Format(true, 2, true));
  return serializer.serialize(doc);
}

/**
 * Serializes a document in compact format
 * @param {Document} doc
 * @returns {string}
 */
function toStringCompact(doc) {
  const serializer = new Serializer(new Format(false, 0, false));
  return serializer.serialize(doc);
}

/**
 * Serializes a document with custom format
 * @param {Document} doc
 * @param {Format} format
 * @returns {string}
 */
function toStringWithFormat(doc, format) {
  const serializer = new Serializer(format);
  return serializer.serialize(doc);
}

root.xcdn = Object.freeze({ version: "1.0.1", parseStr, toStringPretty, toStringCompact, Document, Node, XObject, XArray, XString, Int, Float, Bool, Null, DecimalValue, Bytes, DateTime, Duration, Uuid, Tag, Annotation, XCDNError,
  parse: parseStr, stringify: toStringPretty, stringifyCompact: toStringCompact });
})(typeof window !== "undefined" ? window : globalThis);
