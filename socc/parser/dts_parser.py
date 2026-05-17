"""Device Tree Source (DTS) file parser."""

import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum


class TokenType(Enum):
    """Token type enumeration."""
    KEYWORD = "KEYWORD"  # /dts-v1/, /include/, /omit-if-no-ref/
    NODE_START = "NODE_START"  # {
    NODE_END = "NODE_END"  # }
    EQUALS = "EQUALS"  # =
    SEMICOLON = "SEMICOLON"  # ;
    COMMA = "COMMA"  # ,
    LPAREN = "LPAREN"  # (
    RPAREN = "RPAREN"  # )
    LANGLE = "LANGLE"  # <
    RANGLE = "RANGLE"  # >
    STRING = "STRING"  # "string"
    NUMBER = "NUMBER"  # 0x123, 123
    PHANDLE = "PHANDLE"  # &ref_name
    LABEL = "LABEL"  # label:
    NODE_NAME = "NODE_NAME"  # node_name or node@addr
    SLASH = "SLASH"  # /
    EOF = "EOF"


@dataclass
class Token:
    """A single lexer token."""
    type: TokenType
    value: str
    line: int
    column: int


class DTSTokenizer:
    """DTS file tokenizer."""
    
    def __init__(self, content: str):
        self.content = content
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens: List[Token] = []
    
    def _advance(self) -> Optional[str]:
        """Advance by one character and return it."""
        if self.pos >= len(self.content):
            return None
        char = self.content[self.pos]
        self.pos += 1
        if char == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char
    
    def _peek(self, offset: int = 0) -> Optional[str]:
        """Peek ahead without consuming."""
        pos = self.pos + offset
        if pos >= len(self.content):
            return None
        return self.content[pos]
    
    def _skip_whitespace(self) -> None:
        """Skip whitespace characters."""
        while self._peek() and self._peek() in ' \t\n\r':
            self._advance()
    
    def _skip_comment(self) -> None:
        """Skip /* */ and // comments."""
        if self._peek() == '/' and self._peek(1) == '*':
            # block comment
            self._advance()  # /
            self._advance()  # *
            while True:
                if self._peek() is None:
                    raise SyntaxError(f"Unclosed comment at line {self.line}")
                if self._peek() == '*' and self._peek(1) == '/':
                    self._advance()  # *
                    self._advance()  # /
                    break
                self._advance()
        elif self._peek() == '/' and self._peek(1) == '/':
            # line comment
            while self._peek() and self._peek() != '\n':
                self._advance()
            if self._peek() == '\n':
                self._advance()
    
    def _read_string(self) -> str:
        """Read a quoted string, handling escape sequences."""
        quote = self._advance()  # "
        result = []
        while True:
            char = self._peek()
            if char is None:
                raise SyntaxError(f"Unclosed string literal at line {self.line}")
            if char == '\\':
                self._advance()
                next_char = self._advance()
                if next_char == 'n':
                    result.append('\n')
                elif next_char == 't':
                    result.append('\t')
                elif next_char == '\\':
                    result.append('\\')
                elif next_char == '"':
                    result.append('"')
                else:
                    result.append(next_char or '')
            elif char == '"':
                self._advance()
                break
            else:
                result.append(self._advance() or '')
        return ''.join(result)
    
    def _read_number(self) -> str:
        """Read a hexadecimal or decimal number literal."""
        result = []
        # hexadecimal
        if self._peek() == '0' and self._peek(1) in 'xX':
            result.append(self._advance())  # 0
            result.append(self._advance())  # x
            while self._peek() and self._peek() in '0123456789abcdefABCDEF':
                result.append(self._advance() or '')
        else:
            # decimal
            while self._peek() and self._peek() in '0123456789':
                result.append(self._advance() or '')
        return ''.join(result)
    
    def _read_identifier(self) -> str:
        """Read an identifier (node name, property name, etc.)."""
        result = []
        while self._peek() and (self._peek().isalnum() or self._peek() in '_-,@#'):
            result.append(self._advance() or '')
        return ''.join(result)
    
    def tokenize(self) -> List[Token]:
        """Tokenize DTS content into a list of tokens."""
        while self.pos < len(self.content):
            self._skip_whitespace()
            
            if self.pos >= len(self.content):
                break
            
            # skip comment
            if self._peek() == '/' and self._peek(1) in '*/' :
                self._skip_comment()
                continue
            
            line = self.line
            col = self.column
            char = self._peek()
            
            # keyword / identifier
            if char == '/' and self._peek(1) and (self._peek(1).isalpha() or self._peek(1) == '_'):
                self._advance()  # /
                keyword = self._read_identifier()
                self.tokens.append(Token(TokenType.KEYWORD, '/' + keyword, line, col))
            
            # string
            elif char == '"':
                string_val = self._read_string()
                self.tokens.append(Token(TokenType.STRING, string_val, line, col))
            
            # hex or decimal number
            elif char == '0' and self._peek(1) in 'xX':
                num = self._read_number()
                self.tokens.append(Token(TokenType.NUMBER, num, line, col))
            elif char and char.isdigit():
                num = self._read_number()
                self.tokens.append(Token(TokenType.NUMBER, num, line, col))
            
            # phandle reference
            elif char == '&':
                self._advance()  # &
                ref = self._read_identifier()
                self.tokens.append(Token(TokenType.PHANDLE, '&' + ref, line, col))
            
            # single-char operators
            elif char == '{':
                self._advance()
                self.tokens.append(Token(TokenType.NODE_START, '{', line, col))
            elif char == '}':
                self._advance()
                self.tokens.append(Token(TokenType.NODE_END, '}', line, col))
            elif char == ';':
                self._advance()
                self.tokens.append(Token(TokenType.SEMICOLON, ';', line, col))
            elif char == '=':
                self._advance()
                self.tokens.append(Token(TokenType.EQUALS, '=', line, col))
            elif char == ',':
                self._advance()
                self.tokens.append(Token(TokenType.COMMA, ',', line, col))
            elif char == '(':
                self._advance()
                self.tokens.append(Token(TokenType.LPAREN, '(', line, col))
            elif char == ')':
                self._advance()
                self.tokens.append(Token(TokenType.RPAREN, ')', line, col))
            elif char == '<':
                self._advance()
                self.tokens.append(Token(TokenType.LANGLE, '<', line, col))
            elif char == '>':
                self._advance()
                self.tokens.append(Token(TokenType.RANGLE, '>', line, col))
            elif char == '/':
                self._advance()
                self.tokens.append(Token(TokenType.SLASH, '/', line, col))
            
            # identifier (node name / property name)
            elif char and (char.isalpha() or char in '_#'):
                ident = self._read_identifier()
                # check for a label (followed by colon)
                if self._peek() == ':':
                    self._advance()
                    self.tokens.append(Token(TokenType.LABEL, ident, line, col))
                else:
                    self.tokens.append(Token(TokenType.NODE_NAME, ident, line, col))
            
            else:
                raise SyntaxError(f"Unexpected character {char!r} at line {self.line}, column {self.column}")
        
        self.tokens.append(Token(TokenType.EOF, '', self.line, self.column))
        return self.tokens


class DTSParser:
    """DTS parser: converts token list into a nested dict tree."""
    
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
    
    def _current(self) -> Token:
        """Return the current token."""
        if self.pos >= len(self.tokens):
            return self.tokens[-1]  # EOF
        return self.tokens[self.pos]
    
    def _peek(self, offset: int = 0) -> Token:
        """Peek at an upcoming token."""
        pos = self.pos + offset
        if pos >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[pos]
    
    def _advance(self) -> Token:
        """Consume and return the current token."""
        token = self._current()
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return token
    
    def _expect(self, token_type: TokenType) -> Token:
        """Consume the current token, raising SyntaxError if type doesn't match."""
        token = self._current()
        if token.type != token_type:
            raise SyntaxError(
                f"Expected {token_type}, got {token.type} ({token.value!r}) "
                f"at line {token.line}, column {token.column}"
            )
        return self._advance()
    
    def parse(self) -> Dict[str, Any]:
        """Parse the entire DTS file into a nested dict."""
        root = {"type": "root", "children": [], "properties": {}}
        
        # parse header (/dts-v1/)
        if self._current().type == TokenType.KEYWORD:
            # may be /dts-v1/ or other keywords
            self._advance()
            if self._current().type == TokenType.SEMICOLON:
                self._advance()
        
        # skip remaining directives
        while self._current().type == TokenType.KEYWORD:
            self._advance()
            # advance to semicolon
            while self._current().type != TokenType.SEMICOLON and self._current().type != TokenType.EOF:
                self._advance()
            if self._current().type == TokenType.SEMICOLON:
                self._advance()
        
        # parse the node tree
        while self._current().type != TokenType.EOF:
            if self._current().type == TokenType.SLASH:
                # root node / { ... }
                self._advance()
                if self._current().type == TokenType.NODE_START:
                    self._advance()
                    node = {"type": "node", "name": "/", "properties": {}, "children": []}
                    self._parse_node_content_into(node)
                    self._expect(TokenType.NODE_END)
                    if self._current().type == TokenType.SEMICOLON:
                        self._advance()
                    root["children"].append(node)
            elif self._current().type == TokenType.NODE_NAME or self._current().type == TokenType.LABEL:
                # label or child node
                node = self._parse_node()
                root["children"].append(node)
            else:
                self._advance()
        
        return root
    
    def _parse_node(self) -> Dict[str, Any]:
        """Parse a single DTS node."""
        # optional label
        label = None
        if self._current().type == TokenType.LABEL:
            label = self._current().value
            self._advance()
        
        # node name
        node_name = self._expect(TokenType.NODE_NAME).value
        
        node = {
            "type": "node",
            "name": node_name,
            "label": label,
            "properties": {},
            "children": []
        }
        
        # optional @address suffix
        if self._current().type == TokenType.LANGLE:
            self._advance()
            # skip address content
            while self._current().type != TokenType.RANGLE:
                self._advance()
            self._expect(TokenType.RANGLE)
            node["address"] = ""  # simplified
        
        self._expect(TokenType.NODE_START)
        self._parse_node_content_into(node)
        self._expect(TokenType.NODE_END)
        self._expect(TokenType.SEMICOLON)
        
        return node
    
    def _parse_node_content(self) -> Dict[str, Any]:
        """Parse node body and return it as a dict."""
        node = {"properties": {}, "children": []}
        self._parse_node_content_into(node)
        return node
    
    def _parse_node_content_into(self, node: Dict[str, Any]) -> None:
        """Parse node body, filling properties and children into *node*."""
        while self._current().type != TokenType.NODE_END:
            if self._current().type == TokenType.EOF:
                raise SyntaxError("Unclosed node")
            
            # label
            if self._current().type == TokenType.LABEL:
                label = self._current().value
                self._advance()
                # followed by node name or property
                if self._current().type == TokenType.NODE_NAME:
                    node_name = self._current().value
                    self._advance()
                    if self._current().type == TokenType.NODE_START:
                        # child node
                        self._advance()
                        child = {"type": "node", "name": node_name, "label": label, "properties": {}, "children": []}
                        self._parse_node_content_into(child)
                        self._expect(TokenType.NODE_END)
                        self._expect(TokenType.SEMICOLON)
                        node["children"].append(child)
                    else:
                        pass  # unsupported format
            
            # node or property
            elif self._current().type == TokenType.NODE_NAME:
                node_name = self._current().value
                self._advance()
                
                if self._current().type == TokenType.NODE_START:
                    # child node
                    self._advance()
                    child = {"type": "node", "name": node_name, "properties": {}, "children": []}
                    self._parse_node_content_into(child)
                    self._expect(TokenType.NODE_END)
                    self._expect(TokenType.SEMICOLON)
                    node["children"].append(child)
                elif self._current().type == TokenType.EQUALS:
                    # property
                    self._advance()  # =
                    prop_value = self._parse_property_value()
                    node["properties"][node_name] = prop_value
                    self._expect(TokenType.SEMICOLON)
                else:
                    pass  # unsupported format, skip
            
            else:
                self._advance()
    
    def _parse_property_value(self) -> Any:
        """Parse a property value up to the next semicolon."""
        values = []
        
        # parse value tokens until semicolon
        while self._current().type != TokenType.SEMICOLON:
            if self._current().type == TokenType.EOF:
                raise SyntaxError("Unclosed property value")
            
            if self._current().type == TokenType.STRING:
                values.append(self._current().value)
                self._advance()
            elif self._current().type == TokenType.NUMBER:
                values.append(int(self._current().value, 0))
                self._advance()
            elif self._current().type == TokenType.PHANDLE:
                values.append(self._current().value)
                self._advance()
            elif self._current().type == TokenType.LANGLE:
                # angle-bracket array < ... >
                self._advance()
                array = []
                while self._current().type != TokenType.RANGLE:
                    if self._current().type == TokenType.NUMBER:
                        array.append(int(self._current().value, 0))
                    elif self._current().type == TokenType.PHANDLE:
                        array.append(self._current().value)
                    elif self._current().type == TokenType.NODE_NAME:
                        # macro constant
                        array.append(self._current().value)
                    self._advance()
                self._expect(TokenType.RANGLE)
                values.append(array)
            elif self._current().type == TokenType.COMMA:
                self._advance()
            else:
                self._advance()
        
        # return an appropriately typed value
        if len(values) == 0:
            return True  # empty property
        elif len(values) == 1:
            return values[0]
        else:
            return values


def parse_dts(content: str) -> Dict[str, Any]:
    """Parse DTS text and return the nested dict tree."""
    tokenizer = DTSTokenizer(content)
    tokens = tokenizer.tokenize()
    
    parser = DTSParser(tokens)
    return parser.parse()
