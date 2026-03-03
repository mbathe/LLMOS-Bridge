# Example Module — Action Reference

## say_hello

Generate a greeting for a person, with optional formal mode.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| name | string | Yes | — | Name of the person to greet |
| formal | boolean | No | false | Use a formal greeting style |

### Returns

```json
{
  "greeting": "Hello, Alice!",
  "formal": false
}
```

### Examples

```yaml
# Informal greeting
- id: greet_informal
  module: example
  action: say_hello
  params:
    name: "Alice"

# Formal greeting
- id: greet_formal
  module: example
  action: say_hello
  params:
    name: "Dr. Smith"
    formal: true
```

### Security

- Permission: `readonly`
- Risk Level: Low

---

## count_words

Count the number of words in a text string.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| text | string | Yes | — | Text to count words in |
| include_punctuation | boolean | No | false | Count punctuation marks as part of words |

### Returns

```json
{
  "text": "Hello world, this is a test.",
  "word_count": 6
}
```

### Examples

```yaml
- id: count
  module: example
  action: count_words
  params:
    text: "Hello world, this is a test."
```

### Security

- Permission: `readonly`
- Risk Level: Low
