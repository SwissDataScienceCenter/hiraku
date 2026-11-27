# Record

[View source on GitHub](https://github.com/SwissDataScienceCenter/hiraku/blob/main/src/hiraku/record.py)

Records hold document data. Use the project's Vault for filesystem access and
collection operations. Pass `overwrite=True` to replace an existing record.

## Record

::: hiraku.record.Record
    options:
      members:
        - type
        - title
        - description
        - status
        - resource
        - tags
        - generated
        - verified
        - id
        - markdown
        - parent_id
        - related_to
        - commit
        - decision
        - branch
        - state
        - metadata
      show_if_no_docstring: true
      show_signature_annotations: true

## Vault

::: hiraku.record.Vault
    options:
      members:
        - root
        - file_path
        - load
        - create
        - list
        - children
        - descendants
        - save
        - update
        - delete
        - validate
      show_if_no_docstring: true
      show_signature_annotations: true
